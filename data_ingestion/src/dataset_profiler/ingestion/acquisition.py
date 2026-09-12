from __future__ import annotations

import hashlib
import ipaddress
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .contracts import ChecksumAlgorithm, ManifestAsset, SourceKind


class AcquisitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AcquiredAsset:
    asset_id: str
    size_bytes: int
    content_sha256: str
    content_path: Path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_zenodo_url(raw_url: str, *, validate_dns: bool) -> None:
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() not in {"zenodo.org", "www.zenodo.org"}
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise AcquisitionError("Zenodo asset URL is not allowlisted")
    if not validate_dns:
        return
    try:
        records = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        addresses = {record[4][0] for record in records}
    except OSError as exc:
        raise AcquisitionError("Zenodo host could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise AcquisitionError("Zenodo host resolved to a non-public address")


class ZenodoAcquirer:
    def __init__(
        self,
        cache_dir: Path,
        *,
        max_asset_bytes: int = 25_000_000_000,
        client: httpx.Client | None = None,
        validate_dns: bool = True,
    ):
        self.cache_dir = cache_dir.resolve()
        self.staging_dir = self.cache_dir / "staging"
        self.content_dir = self.cache_dir / "content"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.content_dir.mkdir(parents=True, exist_ok=True)
        self.max_asset_bytes = max_asset_bytes
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=False)
        self._owns_client = client is None
        self.validate_dns = validate_dns

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def has_verified_content(self, content_sha256: str, size_bytes: int) -> bool:
        target = self.content_dir / content_sha256[:2] / content_sha256
        try:
            return (
                target.is_file()
                and target.stat().st_size == size_bytes
                and _file_sha256(target) == content_sha256
            )
        except OSError:
            return False

    def acquire(self, source_kind: SourceKind, asset: ManifestAsset) -> AcquiredAsset:
        if source_kind is not SourceKind.ZENODO:
            raise AcquisitionError("Zenodo acquirer received another provider")
        if asset.size_bytes > self.max_asset_bytes:
            raise AcquisitionError("Asset exceeds the configured download limit")
        url = str(asset.download_url)
        _validate_zenodo_url(url, validate_dns=self.validate_dns)
        temporary_path: Path | None = None
        sha256 = hashlib.sha256()
        provider_hash = (
            hashlib.md5(usedforsecurity=False)
            if asset.source_checksum
            and asset.source_checksum.algorithm is ChecksumAlgorithm.MD5
            else hashlib.sha256()
            if asset.source_checksum
            and asset.source_checksum.algorithm is ChecksumAlgorithm.SHA256
            else None
        )
        size = 0
        try:
            with self.client.stream("GET", url) as response:
                if response.is_redirect:
                    raise AcquisitionError("Zenodo download redirected unexpectedly")
                response.raise_for_status()
                declared = response.headers.get("Content-Length")
                try:
                    declared_size = int(declared) if declared is not None else None
                except ValueError as exc:
                    raise AcquisitionError("Zenodo returned an invalid content length") from exc
                if declared_size is not None and declared_size != asset.size_bytes:
                    raise AcquisitionError(
                        "Zenodo content length differs from the approved manifest"
                    )
                with tempfile.NamedTemporaryFile(dir=self.staging_dir, delete=False) as output:
                    temporary_path = Path(output.name)
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > asset.size_bytes or size > self.max_asset_bytes:
                            raise AcquisitionError("Zenodo download exceeded the approved size")
                        output.write(chunk)
                        sha256.update(chunk)
                        if provider_hash is not None:
                            provider_hash.update(chunk)
            if size != asset.size_bytes:
                raise AcquisitionError("Zenodo download was truncated")
            if (
                asset.source_checksum
                and provider_hash is not None
                and provider_hash.hexdigest() != asset.source_checksum.value
            ):
                raise AcquisitionError("Zenodo provider checksum did not match")
            digest = sha256.hexdigest()
            target_dir = self.content_dir / digest[:2]
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / digest
            if target.exists():
                if target.stat().st_size == size and _file_sha256(target) == digest:
                    pass
                else:
                    temporary_path.replace(target)
                    temporary_path = None
            else:
                temporary_path.replace(target)
                temporary_path = None
            return AcquiredAsset(
                asset_id=asset.asset_id,
                size_bytes=size,
                content_sha256=digest,
                content_path=target,
            )
        except httpx.HTTPError as exc:
            raise AcquisitionError("Zenodo download failed") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
