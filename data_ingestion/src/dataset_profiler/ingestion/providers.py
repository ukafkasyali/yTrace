from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .acquisition import AcquiredAsset, AcquisitionError, ZenodoAcquirer, _file_sha256
from .contracts import ApprovedManifest, ChecksumAlgorithm, ManifestAsset, SourceKind

_GITHUB_LOCATOR = re.compile(r"^github:([^/]+)/([^:]+):blob:([a-f0-9]{40})$")
_HF_LOCATOR = re.compile(r"^huggingface:datasets/([^/]+/[^:]+):([a-f0-9]{40}):(.+)$")
_REDIRECTS = {301, 302, 303, 307, 308}


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _validate_public_url(raw_url: str, allowed_host, *, validate_dns: bool) -> None:
    parsed = urlsplit(raw_url)
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not allowed_host(host)
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise AcquisitionError("Provider asset URL is not allowlisted")
    if not validate_dns:
        return
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = {record[4][0] for record in records}
    except OSError as exc:
        raise AcquisitionError("Provider host could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise AcquisitionError("Provider host resolved to a non-public address")


class ProviderAcquirer:
    def __init__(
        self,
        cache_dir: Path,
        *,
        max_asset_bytes: int = 25_000_000_000,
        client: httpx.Client | None = None,
        validate_dns: bool = True,
        github_token: str | None = None,
        hugging_face_token: str | None = None,
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
        self.github_token = github_token
        self.hugging_face_token = hugging_face_token
        self.zenodo = ZenodoAcquirer(
            cache_dir,
            max_asset_bytes=max_asset_bytes,
            client=self.client,
            validate_dns=validate_dns,
        )

    def close(self) -> None:
        self.zenodo.close()
        if self._owns_client:
            self.client.close()

    def has_verified_content(self, content_sha256: str, size_bytes: int) -> bool:
        return self.zenodo.has_verified_content(content_sha256, size_bytes)

    def acquire(self, manifest: ApprovedManifest, asset: ManifestAsset) -> AcquiredAsset:
        if manifest.source_kind is SourceKind.ZENODO:
            return self.zenodo.acquire(SourceKind.ZENODO, asset)
        if manifest.source_kind is SourceKind.GITHUB:
            return self._github(manifest, asset)
        if manifest.source_kind is SourceKind.HUGGING_FACE:
            return self._hugging_face(manifest, asset)
        raise AcquisitionError("Approved source provider is unsupported")

    def _github(self, manifest: ApprovedManifest, asset: ManifestAsset) -> AcquiredAsset:
        locator = _GITHUB_LOCATOR.fullmatch(asset.provider_locator)
        canonical = urlsplit(str(manifest.canonical_url))
        if locator is None or not _safe_name(asset.name):
            raise AcquisitionError("GitHub asset identity is invalid")
        owner, repo, blob_sha = locator.groups()
        if canonical.path.rstrip("/") != f"/{owner}/{repo}" or not re.fullmatch(
            r"[a-f0-9]{40}", manifest.source_revision
        ):
            raise AcquisitionError("GitHub repository or commit identity is invalid")
        api = f"https://api.github.com/repos/{owner}/{repo}"
        expected_download = f"{api}/git/blobs/{blob_sha}"
        if str(asset.download_url).rstrip("/") != expected_download:
            raise AcquisitionError("GitHub blob URL does not match its approved identity")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        metadata_url = (
            f"{api}/contents/{quote(asset.name, safe='/')}?ref={manifest.source_revision}"
        )
        metadata = self._json(metadata_url, headers, lambda host: host == "api.github.com")
        if (
            metadata.get("type") != "file"
            or metadata.get("sha") != blob_sha
            or metadata.get("size") != asset.size_bytes
        ):
            raise AcquisitionError("GitHub commit does not contain the approved blob")
        return self._download(
            expected_download,
            asset,
            headers=headers | {"Accept": "application/vnd.github.raw+json"},
            allowed_host=lambda host: host == "api.github.com",
            expected_git_blob=blob_sha,
        )

    def _hugging_face(self, manifest: ApprovedManifest, asset: ManifestAsset) -> AcquiredAsset:
        locator = _HF_LOCATOR.fullmatch(asset.provider_locator)
        canonical = urlsplit(str(manifest.canonical_url))
        if locator is None or not _safe_name(asset.name):
            raise AcquisitionError("Hugging Face asset identity is invalid")
        namespace, revision, name = locator.groups()
        if (
            canonical.path.rstrip("/") != f"/datasets/{namespace}"
            or revision != manifest.source_revision
            or name != asset.name
        ):
            raise AcquisitionError("Hugging Face dataset revision identity is invalid")
        expected_download = (
            f"https://huggingface.co/datasets/{namespace}/resolve/"
            f"{revision}/{quote(name, safe='/')}"
        )
        if str(asset.download_url).rstrip("/") != expected_download:
            raise AcquisitionError("Hugging Face file URL does not match its approved identity")
        headers = {}
        if self.hugging_face_token:
            headers["Authorization"] = f"Bearer {self.hugging_face_token}"
        return self._download(
            expected_download,
            asset,
            headers=headers,
            allowed_host=lambda host: host == "huggingface.co" or host.endswith(".hf.co"),
            expected_revision=revision,
            reject_lfs_pointer=True,
        )

    def _json(self, url: str, headers: dict[str, str], allowed_host) -> dict:
        _validate_public_url(url, allowed_host, validate_dns=self.validate_dns)
        try:
            response = self.client.get(url, headers=headers)
            if response.status_code in _REDIRECTS:
                raise AcquisitionError("Provider metadata redirected unexpectedly")
            response.raise_for_status()
            if len(response.content) > 1_000_000:
                raise AcquisitionError("Provider metadata exceeded the size limit")
            payload = json.loads(response.content)
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise AcquisitionError("Provider metadata lookup failed") from exc
        if not isinstance(payload, dict):
            raise AcquisitionError("Provider metadata had an invalid shape")
        return payload

    def _download(
        self,
        url: str,
        asset: ManifestAsset,
        *,
        headers: dict[str, str],
        allowed_host,
        expected_git_blob: str | None = None,
        expected_revision: str | None = None,
        reject_lfs_pointer: bool = False,
    ) -> AcquiredAsset:
        if asset.size_bytes > self.max_asset_bytes:
            raise AcquisitionError("Asset exceeds the configured download limit")
        current = url
        observed_revision: str | None = None
        temporary_path: Path | None = None
        try:
            for redirect_count in range(4):
                _validate_public_url(current, allowed_host, validate_dns=self.validate_dns)
                with self.client.stream("GET", current, headers=headers) as response:
                    revision_header = response.headers.get("X-Repo-Commit")
                    if revision_header:
                        if expected_revision and revision_header != expected_revision:
                            raise AcquisitionError("Provider returned another dataset revision")
                        observed_revision = revision_header
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("Location")
                        if not location or redirect_count == 3:
                            raise AcquisitionError("Provider download redirect was invalid")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    declared = response.headers.get("Content-Length")
                    try:
                        declared_size = int(declared) if declared is not None else None
                    except ValueError as exc:
                        raise AcquisitionError("Provider returned an invalid content length") from exc
                    if declared_size is not None and declared_size != asset.size_bytes:
                        raise AcquisitionError("Provider content length differs from the manifest")
                    sha256 = hashlib.sha256()
                    provider_hash = self._provider_hash(asset)
                    git_hash = (
                        hashlib.sha1(f"blob {asset.size_bytes}\0".encode(), usedforsecurity=False)
                        if expected_git_blob
                        else None
                    )
                    prefix = bytearray()
                    size = 0
                    with tempfile.NamedTemporaryFile(dir=self.staging_dir, delete=False) as output:
                        temporary_path = Path(output.name)
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > asset.size_bytes or size > self.max_asset_bytes:
                                raise AcquisitionError("Provider download exceeded the approved size")
                            if len(prefix) < 200:
                                prefix.extend(chunk[: 200 - len(prefix)])
                            output.write(chunk)
                            sha256.update(chunk)
                            if provider_hash is not None:
                                provider_hash.update(chunk)
                            if git_hash is not None:
                                git_hash.update(chunk)
                    break
            else:
                raise AcquisitionError("Provider download exceeded the redirect limit")
            if expected_revision and observed_revision != expected_revision:
                raise AcquisitionError("Provider did not attest the approved dataset revision")
            if size != asset.size_bytes:
                raise AcquisitionError("Provider download was truncated")
            if reject_lfs_pointer and prefix.startswith(b"version https://git-lfs.github.com/spec"):
                raise AcquisitionError("Provider returned a Git LFS pointer instead of content")
            if expected_git_blob and git_hash and git_hash.hexdigest() != expected_git_blob:
                raise AcquisitionError("Downloaded bytes do not match the approved Git blob")
            if asset.source_checksum and provider_hash and (
                provider_hash.hexdigest() != asset.source_checksum.value
            ):
                raise AcquisitionError("Provider checksum did not match")
            digest = sha256.hexdigest()
            target_dir = self.content_dir / digest[:2]
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / digest
            if target.exists() and (
                target.stat().st_size != size or _file_sha256(target) != digest
            ):
                temporary_path.replace(target)
                temporary_path = None
            elif target.exists():
                pass
            else:
                temporary_path.replace(target)
                temporary_path = None
            return AcquiredAsset(asset.asset_id, size, digest, target)
        except httpx.HTTPError as exc:
            raise AcquisitionError("Provider download failed") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _provider_hash(asset: ManifestAsset):
        if asset.source_checksum is None:
            return None
        if asset.source_checksum.algorithm is ChecksumAlgorithm.MD5:
            return hashlib.md5(usedforsecurity=False)
        return hashlib.sha256()
