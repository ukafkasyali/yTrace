from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from dataset_profiler.ingestion.api import create_app
from dataset_profiler.ingestion.jobs import (
    AssetReceipt,
    IngestionJobStore,
    IngestionState,
    ResourceFormat,
    ResourceProfile,
)
from dataset_profiler.ingestion.onboarding import (
    IngestionOnboardingCoordinator,
    OnboardingWorker,
    source_descriptor_from_acquisition,
)
from dataset_profiler.ingestion.service import IngestionService


class UnusedResolver:
    def resolve(self, approved_source_id: str):
        raise AssertionError("the test uses an already persisted approved ingestion")

    def close(self) -> None:
        pass


def prepared_ingestion(root: Path):
    store = IngestionJobStore(root / "ingestions.sqlite3")
    job, _ = store.get_or_create(
        approved_source_id="src_0123456789abcdef01234567",
        manifest_sha256="a" * 64,
        source_url="https://github.com/example/telemetry",
        source_kind="GITHUB",
        source_revision="b" * 40,
        asset_ids=["asset_0123456789abcdef"],
        state=IngestionState.INSPECTING,
        message="inspecting",
        dataset_license_id="MIT",
    )
    rows = ["time," + ",".join(f"joint_{index}" for index in range(1, 8))]
    rows.extend(
        f"{sample / 1000:.3f}," + ",".join(str(sample + index) for index in range(1, 8))
        for sample in range(1_025)
    )
    payload = ("\n".join(rows) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    content = root / "cache" / "content" / digest[:2] / digest
    content.parent.mkdir(parents=True)
    content.write_bytes(payload)
    receipt = store.record_receipt(
        AssetReceipt(
            ingestion_id=job.ingestion_id,
            asset_id="asset_0123456789abcdef",
            provider_locator="github:example/telemetry:file.csv@" + "b" * 40,
            expected_size_bytes=len(payload),
            observed_size_bytes=len(payload),
            content_sha256=digest,
            content_key=f"sha256/{digest[:2]}/{digest}",
            acquired_at=datetime.now(UTC),
        )
    )
    resource = store.record_resource(
        ResourceProfile(
            resource_id="res_0123456789abcdef01234567",
            ingestion_id=job.ingestion_id,
            asset_id=receipt.asset_id,
            logical_path="file.csv",
            size_bytes=len(payload),
            content_sha256=digest,
            format=ResourceFormat.CSV,
            details={
                "columns": [
                    {"name": "time", "dtype": "float64", "nullable": False},
                    *[
                        {"name": f"joint_{index}", "dtype": "float64", "nullable": False}
                        for index in range(1, 8)
                    ],
                ],
                "rowCount": 1_025,
            },
            inspected_at=datetime.now(UTC),
        )
    )
    return store, job, receipt, resource


def test_approved_acquisition_becomes_source_descriptor(tmp_path: Path) -> None:
    store, job, receipt, resource = prepared_ingestion(tmp_path)
    descriptor = source_descriptor_from_acquisition(
        job, [receipt], [resource], cache_dir=tmp_path / "cache"
    )

    assert descriptor.source_type == "local_directory"
    assert descriptor.dataset_id == "trace/approved-0123456789abcdef01234567"
    assert descriptor.revision == "b" * 40
    assert descriptor.provenance["manifest_sha256"] == "a" * 64
    assert descriptor.provenance["asset_receipts"][0]["content_sha256"] == receipt.content_sha256
    assert descriptor.discovery_metadata["resources"][0]["resource_id"] == resource.resource_id
    store.close()


def test_http_human_resolution_resumes_same_orchestrator_job_to_timef(tmp_path: Path) -> None:
    store, job, _, _ = prepared_ingestion(tmp_path)
    coordinator = IngestionOnboardingCoordinator(tmp_path, store)

    paused = coordinator.start(job.ingestion_id)
    assert paused.state is IngestionState.NEEDS_HUMAN_RESOLUTION
    assert paused.onboarding_status == "NEEDS_HUMAN_RESOLUTION"
    assert len(paused.onboarding_blockers) == 7

    service = IngestionService(tmp_path, UnusedResolver())
    # Use the same persisted database through the service-owned store.
    store.close()
    coordinator = IngestionOnboardingCoordinator(tmp_path, service.jobs)
    app = create_app(service=service, onboarding=coordinator)
    with TestClient(app) as client:
        status = client.get(f"/api/ingestions/{job.ingestion_id}/onboarding")
        assert status.status_code == 200
        body = status.json()
        assert body["status"] == "NEEDS_HUMAN_RESOLUTION"
        assert body["job_id"] == paused.onboarding_job_id
        assert set(body["blockers"][0]) >= {
            "field_path",
            "semantic_status",
            "downstream_requirement",
            "candidate",
            "evidence_refs",
            "remaining_uncertainty",
        }

        rejected = client.post(
            f"/api/ingestions/{job.ingestion_id}/human-resolutions",
            json={
                "field_path": body["blockers"][0]["field_path"],
                "value": "newton * meter",
                "approved_by": "mapping-agent",
                "rationale": "agent suggestion",
                "source": "agent_recommendation",
            },
        )
        assert rejected.status_code == 422

        for blocker in body["blockers"]:
            accepted = client.post(
                f"/api/ingestions/{job.ingestion_id}/human-resolutions",
                json={
                    "field_path": blocker["field_path"],
                    "value": "newton * meter",
                    "approved_by": "engineer@example.test",
                    "rationale": "Confirmed from the machine signal dictionary.",
                },
            )
            assert accepted.status_code == 200

        ready = accepted.json()
        assert ready["stage"] == "CONNECTOR_READY"
        assert ready["job_id"] == body["job_id"]

    worker = OnboardingWorker(coordinator)
    completed = worker.run_once()
    assert completed is not None
    assert completed.state is IngestionState.READY
    assert completed.onboarding_status == "COMPLETED"
    assert completed.onboarding_job_id == paused.onboarding_job_id

    receipt = service.jobs.get_final_receipt(job.ingestion_id)
    assert receipt is not None
    assert receipt.validation["status"] == "passed"
    assert receipt.validation["recordCount"] == 1

    with TestClient(create_app(service=service, onboarding=coordinator)) as client:
        public_receipt = client.get(f"/api/ingestions/{job.ingestion_id}/receipt")
        records = client.get(f"/api/ingestions/{job.ingestion_id}/records")
        final_status = client.get(f"/api/ingestions/{job.ingestion_id}/onboarding")
        assert public_receipt.status_code == 200
        assert public_receipt.json()["receiptSha256"] == receipt.receipt_sha256
        assert records.status_code == 200
        assert records.json()["data"][0]["recordId"]
        assert final_status.json()["status"] == "COMPLETED"
        assert final_status.json()["job_id"] == paused.onboarding_job_id
        assert "verification_result" in final_status.json()["artifacts"]

    service.close()
