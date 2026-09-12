from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .contracts import (
    ApprovedManifest,
    AssetRole,
    ManifestContractError,
    parse_manifest,
)
from .jobs import IngestionJob, IngestionJobConflict, IngestionJobStore, IngestionState


class ApprovedSourceResolutionError(ValueError):
    pass


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


class CreateIngestion(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="forbid")

    approved_source_id: str = Field(pattern=r"^src_[a-f0-9]{24}$")
    asset_ids: list[Annotated[str, Field(pattern=r"^asset_[a-f0-9]{16}$")]] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )

    @model_validator(mode="after")
    def asset_ids_are_unique(self) -> CreateIngestion:
        if self.asset_ids is not None and len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError("asset IDs must be unique")
        return self


def canonical_payload_sha256(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class ResolvedApprovedSource:
    approved_source_id: str
    manifest_sha256: str
    manifest: ApprovedManifest


class ApprovedSourceResolver(Protocol):
    def resolve(self, approved_source_id: str) -> ResolvedApprovedSource: ...

    def close(self) -> None: ...


class _ApprovedSourceLookup(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")

    approved_source_id: str = Field(pattern=r"^src_[a-f0-9]{24}$")
    latest_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    is_acquisition_ready: bool


class HttpApprovedSourceResolver:
    def __init__(
        self,
        base_url: str,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        parsed_base = urlsplit(self.base_url)
        if (
            parsed_base.scheme not in {"http", "https"}
            or not parsed_base.hostname
            or parsed_base.username
            or parsed_base.password
            or parsed_base.query
            or parsed_base.fragment
            or (parsed_base.scheme == "http" and parsed_base.hostname not in {"127.0.0.1", "localhost"})
        ):
            raise ValueError("Sourcing API URL must be HTTPS or loopback HTTP without credentials")
        self.client = client or httpx.Client(timeout=5.0, follow_redirects=False)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _json(self, path: str) -> dict:
        try:
            response = self.client.get(f"{self.base_url}{path}")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ApprovedSourceResolutionError("Approved source service is unavailable") from exc
        if not isinstance(payload, dict):
            raise ApprovedSourceResolutionError("Approved source service returned invalid data")
        return payload

    def resolve(self, approved_source_id: str) -> ResolvedApprovedSource:
        detail_payload = self._json(f"/api/approved-sources/{approved_source_id}")
        manifest_payload = self._json(
            f"/api/approved-sources/{approved_source_id}/manifest"
        )
        try:
            detail = _ApprovedSourceLookup.model_validate(detail_payload)
        except ValidationError as exc:
            raise ApprovedSourceResolutionError(
                "Approved source service returned an invalid source summary"
            ) from exc
        if detail.approved_source_id != approved_source_id:
            raise ApprovedSourceResolutionError("Approved source identity did not match the request")
        if not detail.is_acquisition_ready:
            raise ApprovedSourceResolutionError("Approved source lacks acquisition metadata")
        actual_sha256 = canonical_payload_sha256(manifest_payload)
        if actual_sha256 != detail.latest_manifest_sha256:
            raise ApprovedSourceResolutionError("Approved source manifest integrity check failed")
        try:
            manifest = parse_manifest(manifest_payload)
        except ManifestContractError as exc:
            raise ApprovedSourceResolutionError(str(exc)) from exc
        return ResolvedApprovedSource(
            approved_source_id=approved_source_id,
            manifest_sha256=actual_sha256,
            manifest=manifest,
        )


class IngestionService:
    def __init__(
        self,
        data_dir: Path,
        resolver: ApprovedSourceResolver,
    ):
        self.jobs = IngestionJobStore(data_dir / "ingestions.sqlite3")
        self.resolver = resolver

    def close(self) -> None:
        self.jobs.close()
        self.resolver.close()

    def create(self, request: CreateIngestion) -> tuple[IngestionJob, bool]:
        existing = self.jobs.find_by_source(request.approved_source_id)
        if existing is not None:
            if request.asset_ids is not None and sorted(set(request.asset_ids)) != existing.asset_ids:
                raise IngestionJobConflict(
                    "Approved source already has an ingestion with another asset selection"
                )
            if existing.state is IngestionState.FAILED:
                existing = self.jobs.set_state(
                    existing.ingestion_id,
                    IngestionState.QUEUED,
                    "Queued for verified acquisition retry",
                )
            return existing, False

        resolved = self.resolver.resolve(request.approved_source_id)
        data_asset_ids = {
            asset.asset_id
            for asset in resolved.manifest.assets
            if asset.role is AssetRole.DATA
        }
        selected = sorted(request.asset_ids or data_asset_ids)
        if len(selected) != len(set(selected)):
            raise IngestionJobConflict("Asset selection contains duplicate IDs")
        if not selected or any(asset_id not in data_asset_ids for asset_id in selected):
            raise IngestionJobConflict("Asset selection must contain only approved data assets")

        has_dataset_license = bool(resolved.manifest.dataset_license_id)
        state = IngestionState.QUEUED if has_dataset_license else IngestionState.NEEDS_INPUT
        message = (
            "Queued for verified acquisition"
            if has_dataset_license
            else "Dataset-file license must be confirmed before acquisition"
        )
        return self.jobs.get_or_create(
            approved_source_id=request.approved_source_id,
            manifest_sha256=resolved.manifest_sha256,
            source_url=str(resolved.manifest.canonical_url).rstrip("/"),
            source_kind=resolved.manifest.source_kind.value,
            source_revision=resolved.manifest.source_revision,
            asset_ids=selected,
            state=state,
            message=message,
        )
