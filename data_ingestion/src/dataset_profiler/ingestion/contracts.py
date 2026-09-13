from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    model_validator,
)


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


class _WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class ManifestContractError(ValueError):
    pass


class SourceKind(StrEnum):
    GITHUB = "GITHUB"
    ZENODO = "ZENODO"
    HUGGING_FACE = "HUGGING_FACE"


class AssetRole(StrEnum):
    DATA = "DATA"
    DOCUMENTATION = "DOCUMENTATION"
    CHECKSUM = "CHECKSUM"


class ChecksumAlgorithm(StrEnum):
    MD5 = "md5"
    SHA256 = "sha256"


class SourceChecksum(_WireModel):
    algorithm: ChecksumAlgorithm
    value: str = Field(pattern=r"^[a-fA-F0-9]{32,64}$")

    @model_validator(mode="after")
    def digest_matches_algorithm(self) -> SourceChecksum:
        expected = 32 if self.algorithm is ChecksumAlgorithm.MD5 else 64
        if len(self.value) != expected:
            raise ValueError(f"{self.algorithm.value} checksum must contain {expected} hex digits")
        self.value = self.value.casefold()
        return self


class ManifestAsset(_WireModel):
    asset_id: str = Field(pattern=r"^asset_[a-f0-9]{16}$")
    name: str = Field(min_length=1, max_length=1_024)
    role: AssetRole
    size_bytes: int = Field(gt=0)
    provider_locator: str = Field(min_length=1, max_length=2_000)
    download_url: HttpUrl
    source_checksum: SourceChecksum | None = None


class ApprovedManifest(_WireModel):
    schema_version: str = Field(pattern=r"^1\.1$")
    run_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    candidate_id: str = Field(pattern=r"^ds_[a-f0-9]{12}$")
    name: str = Field(min_length=1, max_length=500)
    canonical_url: HttpUrl
    revision: str | None = Field(default=None, max_length=500)
    source_kind: SourceKind
    source_revision: str = Field(min_length=1, max_length=200)
    assets: list[ManifestAsset] = Field(min_length=1, max_length=500)
    license_id: str
    dataset_license_id: str | None = None
    code_license_id: str | None = None
    labels: list[str]
    sample_rate_hz: float | None = Field(default=None, gt=0)
    file_extensions: list[str]
    total_size_bytes: int | None = Field(default=None, ge=0)
    evidence_ids: list[str]
    limitations: list[str]
    approved_at: datetime

    @model_validator(mode="after")
    def assets_match_provider(self) -> ApprovedManifest:
        if len({asset.asset_id for asset in self.assets}) != len(self.assets):
            raise ValueError("manifest asset IDs must be unique")
        allowed_hosts = {
            SourceKind.GITHUB: {"api.github.com"},
            SourceKind.ZENODO: {"zenodo.org", "www.zenodo.org"},
            SourceKind.HUGGING_FACE: {"huggingface.co"},
        }[self.source_kind]
        locator_prefix = {
            SourceKind.GITHUB: "github:",
            SourceKind.ZENODO: "zenodo:",
            SourceKind.HUGGING_FACE: "huggingface:",
        }[self.source_kind]
        canonical = urlsplit(str(self.canonical_url))
        canonical_host = (canonical.hostname or "").casefold()
        canonical_hosts = {
            SourceKind.GITHUB: {"github.com"},
            SourceKind.ZENODO: {"zenodo.org", "www.zenodo.org"},
            SourceKind.HUGGING_FACE: {"huggingface.co"},
        }[self.source_kind]
        if (
            canonical.scheme != "https"
            or canonical_host not in canonical_hosts
            or canonical.username
            or canonical.password
            or canonical.port not in (None, 443)
        ):
            raise ValueError("canonical URL does not match the approved provider")
        for asset in self.assets:
            parsed = urlsplit(str(asset.download_url))
            if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in allowed_hosts:
                raise ValueError("asset download URL does not match the approved provider")
            if parsed.username or parsed.password or parsed.port not in (None, 443):
                raise ValueError("asset download URL contains unsafe authority data")
            if not asset.provider_locator.startswith(locator_prefix):
                raise ValueError("asset locator does not match the approved provider")
        return self

    @property
    def data_assets(self) -> tuple[ManifestAsset, ...]:
        return tuple(asset for asset in self.assets if asset.role is AssetRole.DATA)


def parse_manifest(payload: Any) -> ApprovedManifest:
    try:
        manifest = ApprovedManifest.model_validate(payload)
    except ValidationError as exc:
        raise ManifestContractError("Approved source manifest does not satisfy schema 1.1") from exc
    if not manifest.data_assets:
        raise ManifestContractError("Approved source manifest contains no ingestible data assets")
    return manifest
