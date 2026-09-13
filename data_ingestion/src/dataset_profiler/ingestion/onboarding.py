"""Canonical bridge from verified acquisition into persisted semantic onboarding."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from timenet.client import TimeNet

from ..onboarding import (
    JobStatus,
    OnboardingOrchestrator,
    RepairResult,
    SemanticResult,
    SourceDescriptor,
)
from ..semantic_spec import DownstreamRequirement
from ..semantic_spec.implementation_handoff import HUMAN_CONFIRMATION
from .dispatch import SpecializedDispatcher
from .generic import GenericBuildResult, GenericImportError, GenericTimeFBuilder
from .jobs import (
    AssetReceipt,
    IngestionJob,
    IngestionJobConflict,
    IngestionJobStore,
    IngestionState,
    ResourceProfile,
)
from .mapping import MappingService, MappingSpec
from .worker import final_receipt_for_build


def source_descriptor_from_acquisition(
    job: IngestionJob,
    receipts: list[AssetReceipt],
    resources: list[ResourceProfile],
    *,
    cache_dir: Path,
) -> SourceDescriptor:
    """Bind one approved manifest to the exact verified bytes passed downstream."""
    selected = {item.asset_id for item in receipts}
    if selected != set(job.asset_ids):
        raise ValueError("SourceDescriptor requires a receipt for every selected asset")
    digest = hashlib.sha256(
        json.dumps(
            [(item.asset_id, item.content_sha256) for item in sorted(receipts, key=lambda x: x.asset_id)],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return SourceDescriptor(
        source_type="local_directory",
        dataset_id=f"trace/approved-{job.approved_source_id.removeprefix('src_')}",
        local_path=str(cache_dir.resolve()),
        source_url=job.source_url,
        revision=job.source_revision,
        provenance={
            "approved_source_id": job.approved_source_id,
            "ingestion_id": job.ingestion_id,
            "manifest_sha256": job.manifest_sha256,
            "acquisition_set_sha256": digest,
            "asset_receipts": [
                {
                    "asset_id": item.asset_id,
                    "content_sha256": item.content_sha256,
                    "content_key": item.content_key,
                }
                for item in sorted(receipts, key=lambda x: x.asset_id)
            ],
        },
        discovery_metadata={
            "source_kind": job.source_kind,
            "dataset_license_id": job.dataset_license_id,
            "resources": [
                {
                    "resource_id": item.resource_id,
                    "asset_id": item.asset_id,
                    "logical_path": item.logical_path,
                    "content_sha256": item.content_sha256,
                    "format": item.format.value,
                }
                for item in resources
            ],
        },
    )


class HumanResolutionRequest(BaseModel):
    """An explicit human implementation decision; never a DatasetSpec mutation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    field_path: str = Field(min_length=1, max_length=500)
    value: Any
    approved_by: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=2_000)
    source: Literal["human_confirmation"] = HUMAN_CONFIRMATION

    @field_validator("value")
    @classmethod
    def value_is_concrete(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("human resolution value must be concrete")
        return value


class IngestionOnboardingBackend:
    """Adapt existing inventory, mapping and TimeF code to orchestrator stages."""

    downstream_context: dict[str, Any] | None

    def __init__(
        self,
        ingestion: IngestionJob,
        jobs: IngestionJobStore,
        builder: GenericTimeFBuilder,
        dispatcher: SpecializedDispatcher,
    ) -> None:
        self.ingestion_id = ingestion.ingestion_id
        self.jobs = jobs
        self.builder = builder
        self.dispatcher = dispatcher
        self.workflow_id = f"approved-source-ingestion-v1:{ingestion.ingestion_id}"
        self.resources = jobs.list_resources(ingestion.ingestion_id)
        self.specialized = dispatcher.resolve(ingestion)
        self.mapping = self._mapping_candidate(ingestion)
        self.requirements = self._requirements()
        self.downstream_context = {
            "ingestion_id": ingestion.ingestion_id,
            "approved_source_id": ingestion.approved_source_id,
            "mode": "specialized_connector" if self.specialized else "generic_timef_connector",
        }

    def _mapping_candidate(self, ingestion: IngestionJob) -> MappingSpec | None:
        if self.specialized is not None:
            return None
        confirmed = self.jobs.get_mapping_payload(ingestion.ingestion_id)
        if confirmed is not None:
            return MappingSpec.model_validate(confirmed)
        candidates = [
            candidate
            for proposal in MappingService(self.jobs).proposals(ingestion.ingestion_id)
            for candidate in proposal.candidates
        ]
        if len(candidates) != 1:
            raise GenericImportError(
                "Semantic onboarding requires exactly one deterministic structural mapping candidate"
            )
        return candidates[0]

    def _requirements(self) -> tuple[DownstreamRequirement, ...]:
        if self.mapping is None:
            return ()
        return tuple(
            DownstreamRequirement(
                field_path=f"signals[{index}].unit",
                downstream_system="TimeF TimeSeriesSpec.unit_value",
                requirement="TimeF requires a concrete Pint-compatible channel unit.",
                best_supported_candidate=channel.unit,
                remaining_uncertainty=(
                    None if channel.unit else "The approved source did not establish this channel unit."
                ),
            )
            for index, channel in enumerate(self.mapping.channels)
            if channel.unit is None
        )

    def profile(self, job) -> dict[str, Any]:
        return {
            "schema_version": "approved-acquisition-profile-v1",
            "dataset_id": job.source.dataset_id,
            "source": job.source.to_dict(),
            "resources": [item.model_dump(mode="json", by_alias=True) for item in self.resources],
        }

    def semantic_analysis(self, job, profile_path: Path) -> SemanticResult:
        signals = []
        source_variables = []
        if self.mapping is not None:
            source_variables.append({"name": self.mapping.time_selector, "role": "time"})
            for index, channel in enumerate(self.mapping.channels):
                variable = f"mapped_channel_{index}"
                source_variables.append({"name": variable, "role": "signal"})
                unit = channel.unit
                signals.append(
                    {
                        "source_variable": variable,
                        "semantic_type": channel.name,
                        "channels": {
                            "count": 1,
                            "source_indices": [0],
                            "target_names": [channel.name],
                        },
                        "dtype": None,
                        "observed_dtypes": [],
                        "unit": {
                            "name": unit,
                            "symbol": unit,
                            "resolution": {"status": "observed" if unit else "unresolved"},
                        },
                        "sampling": {"rate_hz": None, "time_axis": "source_time"},
                        "semantics": {"status": "observed"},
                    }
                )
        spec = {
            "schema_version": "0.2",
            "identity": {
                "dataset_id": job.source.dataset_id,
                "source_subsets": [job.source.revision or "approved-source"],
                "compatible_profile_ids": [],
            },
            "record_discovery": {
                "record_unit": "record",
                "boundary": "approved resource mapping",
                "included_run_ids": [],
            },
            "source_variables": source_variables,
            "signals": signals,
            "time_axes": (
                [{
                    "name": "source_time",
                    "source_variable": self.mapping.time_selector,
                    "kind": "irregular",
                    "unit": "second",
                    "monotonic": True,
                }]
                if self.mapping is not None
                else []
            ),
            "events": [],
            "provenance": [],
            "tasks": [],
            "record_defaults": {"subject_ids": [], "start_time": None},
        }
        return SemanticResult(spec, {"mode": "deterministic_inventory_mapping", "tool_calls": []}, ())

    def validate(self, profile_path: Path, spec_path: Path, evidence_ids: set[str]) -> dict[str, Any]:
        from ..semantic_spec import DatasetSpec

        DatasetSpec.read_json(spec_path)
        if self.mapping is not None:
            resource = next(item for item in self.resources if item.resource_id == self.mapping.resource_id)
            MappingService._validate_against_profile(self.mapping, resource)
        return {"valid": True, "errors": [], "validator": "deterministic-ingestion-structure-v1"}

    def repair(self, job, profile_path, spec_path, evidence_ids) -> RepairResult:
        raise RuntimeError("deterministic ingestion semantic candidates are not model-repaired")

    def implement_connector(self, job, handoff_path: Path, job_dir: Path) -> dict[str, Any]:
        if self.specialized is not None:
            return {"passed": True, "connector": "existing-specialized-kuka-connector"}
        assert self.mapping is not None
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        view = handoff.get("implementation_view", {})
        channels = []
        for index, channel in enumerate(self.mapping.channels):
            value = channel.unit or view.get(f"signals[{index}].unit", {}).get("value")
            if isinstance(value, dict):
                value = value.get("timef_unit") or value.get("symbol") or value.get("name")
            if not isinstance(value, str) or not value.strip():
                raise GenericImportError(f"Channel {channel.name!r} has no approved TimeF unit")
            channels.append(channel.model_copy(update={"unit": value.strip()}))
        current = self.jobs.get(self.ingestion_id)
        mapping = self.mapping.model_copy(
            update={"job_revision": current.job_revision, "channels": channels}
        )
        mapping.require_confirmation_fields()
        resource = next(item for item in self.resources if item.resource_id == mapping.resource_id)
        MappingService._validate_against_profile(mapping, resource)
        payload = mapping.model_dump(mode="json", by_alias=True)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.jobs.confirm_mapping(
            ingestion_id=self.ingestion_id,
            expected_job_revision=current.job_revision,
            resource_id=mapping.resource_id,
            resource_sha256=mapping.resource_sha256,
            mapping_sha256=digest,
            mapping_payload=payload,
            next_state=IngestionState.ONBOARDING,
            next_message="Connector mapping accepted from human-approved onboarding handoff",
        )
        return {"passed": True, "mappingSha256": digest, "mapping": payload}

    def test_connector(self, job, job_dir: Path) -> dict[str, Any]:
        if self.specialized is not None:
            return {"passed": True, "connector": "existing-specialized-kuka-connector"}
        mapping = MappingSpec.model_validate(self.jobs.get_mapping_payload(self.ingestion_id))
        resource = next(item for item in self.resources if item.resource_id == mapping.resource_id)
        records = self.builder.normalize(
            self.builder._resource_path(resource, self.jobs.list_receipts(self.ingestion_id)),
            resource,
            mapping,
        )
        return {"passed": True, "recordCount": len(records)}

    def build(self, job, job_dir: Path) -> dict[str, Any]:
        ingestion = self.jobs.get(self.ingestion_id)
        receipts = self.jobs.list_receipts(self.ingestion_id)
        if self.specialized is not None:
            result = self.dispatcher.build(ingestion, self.specialized, receipts)
        else:
            mapping = MappingSpec.model_validate(self.jobs.get_mapping_payload(self.ingestion_id))
            resource = next(item for item in self.resources if item.resource_id == mapping.resource_id)
            result = self.builder.build(ingestion, resource, mapping, receipts)
        return {"passed": True, **self._result_dict(result)}

    def load(self, job, job_dir: Path) -> dict[str, Any]:
        result = self._saved_result(job, job_dir)
        loaded = TimeNet(registry=self.builder.registry_root).load(
            result.dataset_id, version=result.dataset_version, auto_build=False
        )
        return {"passed": True, "recordCount": len(loaded.records)}

    def verify(self, job, job_dir: Path) -> dict[str, Any]:
        ingestion = self.jobs.get(self.ingestion_id)
        result = self._saved_result(job, job_dir)
        receipts = self.jobs.list_receipts(self.ingestion_id)
        if self.specialized is not None:
            mapping_content = self.dispatcher.receipt_mapping(self.specialized)
            resource_content = self._resource_receipt_content()
        else:
            mapping = MappingSpec.model_validate(self.jobs.get_mapping_payload(self.ingestion_id))
            mapping_content = mapping.model_dump(mode="json", by_alias=True)
            resource = next(item for item in self.resources if item.resource_id == mapping.resource_id)
            resource_content = {
                "resourceId": resource.resource_id,
                "assetId": resource.asset_id,
                "logicalPath": resource.logical_path,
                "sizeBytes": resource.size_bytes,
                "contentSha256": resource.content_sha256,
                "format": resource.format.value,
            }
        receipt = final_receipt_for_build(
            ingestion,
            result,
            mapping_content=mapping_content,
            resource_content=resource_content,
            receipts=receipts,
            registry_root=self.builder.registry_root,
        )
        self.jobs.record_final_receipt(receipt)
        return {"passed": True, "receiptSha256": receipt.receipt_sha256}

    def _resource_receipt_content(self) -> dict[str, Any]:
        return {
            "mode": "SPECIALIZED_CONNECTOR",
            "resourceCount": len(self.resources),
            "resourceSha256": hashlib.sha256(
                json.dumps(
                    [
                        {
                            "resourceId": item.resource_id,
                            "contentSha256": item.content_sha256,
                            "logicalPath": item.logical_path,
                        }
                        for item in self.resources
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        }

    @staticmethod
    def _result_dict(result: GenericBuildResult) -> dict[str, Any]:
        return {
            "dataset_id": result.dataset_id,
            "dataset_version": result.dataset_version,
            "version_dir": str(result.version_dir),
            "record_count": result.record_count,
            "series_count": result.series_count,
            "value_count": result.value_count,
            "validation_sha256": result.validation_sha256,
        }

    @staticmethod
    def _saved_result(job, job_dir: Path) -> GenericBuildResult:
        raw = json.loads(job.artifact_path(job_dir, "build_result").read_text(encoding="utf-8"))
        return GenericBuildResult(
            dataset_id=raw["dataset_id"],
            dataset_version=raw["dataset_version"],
            version_dir=Path(raw["version_dir"]),
            record_count=raw["record_count"],
            series_count=raw["series_count"],
            value_count=raw["value_count"],
            validation_sha256=raw["validation_sha256"],
        )


class IngestionOnboardingCoordinator:
    """Own the one persisted orchestrator job associated with an ingestion."""

    def __init__(self, data_dir: Path, jobs: IngestionJobStore) -> None:
        self.data_dir = data_dir.resolve()
        self.jobs = jobs
        self.cache_dir = self.data_dir / "cache"
        self.registry_root = self.data_dir / "timef"

    @staticmethod
    def onboarding_job_id(ingestion_id: str) -> str:
        return f"job-{ingestion_id.replace('-', '')}"

    def _service(self, ingestion_id: str) -> OnboardingOrchestrator:
        ingestion = self.jobs.get(ingestion_id)
        backend = IngestionOnboardingBackend(
            ingestion,
            self.jobs,
            GenericTimeFBuilder(cache_dir=self.cache_dir, registry_root=self.registry_root),
            SpecializedDispatcher(cache_dir=self.cache_dir, registry_root=self.registry_root),
        )
        return OnboardingOrchestrator(self.data_dir / "onboarding_jobs", backend)

    def can_onboard(self, ingestion_id: str) -> bool:
        try:
            self._service(ingestion_id)
        except GenericImportError:
            return False
        return True

    def start(self, ingestion_id: str) -> IngestionJob:
        ingestion = self.jobs.get(ingestion_id)
        service = self._service(ingestion_id)
        job_id = self.onboarding_job_id(ingestion_id)
        directory = service.store.job_dir(job_id)
        if directory.exists():
            onboarding = service.get_job(job_id)
        else:
            descriptor = source_descriptor_from_acquisition(
                ingestion,
                self.jobs.list_receipts(ingestion_id),
                self.jobs.list_resources(ingestion_id),
                cache_dir=self.cache_dir,
            )
            onboarding = service.create_job(descriptor, job_id=job_id)
        self._sync(ingestion_id, onboarding)
        onboarding = service.run_job(job_id)
        return self._sync(ingestion_id, onboarding)

    def continue_job(self, ingestion_id: str) -> IngestionJob:
        service = self._service(ingestion_id)
        onboarding = service.continue_job(self.onboarding_job_id(ingestion_id))
        return self._sync(ingestion_id, onboarding)

    def resolve(self, ingestion_id: str, request: HumanResolutionRequest) -> dict[str, Any]:
        service = self._service(ingestion_id)
        onboarding = service.resolve_blocker(
            self.onboarding_job_id(ingestion_id),
            field_path=request.field_path,
            value=request.value,
            approved_by=request.approved_by,
            rationale=request.rationale,
            source=request.source,
        )
        self._sync(ingestion_id, onboarding)
        return self.view(ingestion_id)

    def view(self, ingestion_id: str) -> dict[str, Any]:
        ingestion = self.jobs.get(ingestion_id)
        if not ingestion.onboarding_job_id:
            raise IngestionJobConflict("Ingestion has not reached semantic onboarding")
        job = self._service(ingestion_id).get_job(ingestion.onboarding_job_id)
        return {
            "ingestion_id": ingestion_id,
            "job_id": job.job_id,
            "status": job.status.value.upper(),
            "stage": job.stage.value.upper(),
            "blockers": [asdict(item) for item in job.blockers],
            "artifacts": {
                name: {
                    "name": ref.name,
                    "stage": ref.stage.upper(),
                    "media_type": ref.media_type,
                    "sha256": ref.sha256,
                }
                for name, ref in job.artifacts.items()
            },
            "error": asdict(job.error) if job.error else None,
            "result": job.result,
        }

    def _sync(self, ingestion_id: str, job) -> IngestionJob:
        blockers = [asdict(item) for item in job.blockers]
        if job.status is JobStatus.NEEDS_HUMAN_RESOLUTION:
            state = IngestionState.NEEDS_HUMAN_RESOLUTION
            message = "Semantic onboarding needs explicit human resolution"
        elif job.status is JobStatus.COMPLETED:
            state = IngestionState.READY
            message = "TimeF dataset passed orchestrated load and verification"
        elif job.status is JobStatus.FAILED:
            state = IngestionState.FAILED
            message = f"Onboarding failed during {job.error.stage if job.error else job.stage.value}"
        elif job.stage.value == "connector_ready":
            state = IngestionState.CONNECTOR_READY
            message = "Human resolutions accepted; connector stages are queued"
        else:
            state = IngestionState.ONBOARDING
            message = f"Onboarding stage: {job.stage.value}"
        return self.jobs.set_onboarding_state(
            ingestion_id,
            onboarding_job_id=job.job_id,
            onboarding_status=job.status.value.upper(),
            onboarding_stage=job.stage.value.upper(),
            blockers=blockers,
            state=state,
            message=message,
        )


class OnboardingWorker:
    def __init__(self, coordinator: IngestionOnboardingCoordinator) -> None:
        self.coordinator = coordinator

    def recover_interrupted(self) -> int:
        return self.coordinator.jobs.requeue_interrupted_onboarding()

    def run_once(self) -> IngestionJob | None:
        job = self.coordinator.jobs.claim_next_onboarding()
        if job is None:
            return None
        try:
            if job.onboarding_job_id:
                return self.coordinator.continue_job(job.ingestion_id)
            return self.coordinator.start(job.ingestion_id)
        except Exception:
            self.coordinator.jobs.set_state(
                job.ingestion_id,
                IngestionState.FAILED,
                "Onboarding failed because the worker encountered an internal error",
            )
            raise
