from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from data_sourcing.adapters.canonicalize import canonical_source_url
from data_sourcing.adapters.discovery import SourceUnavailable
from data_sourcing.config import Settings
from data_sourcing.models import (
    AssetRole,
    ChecksumAlgorithm,
    DatasetCandidate,
    DatasetProfile,
    EvidenceRecord,
    SourceAsset,
    SourceChecksum,
    SourceKind,
    VerificationStatus,
)
from data_sourcing.security import resolve_public_host, validate_source_url

_RELATED_URL = re.compile(
    r"https://(?:github\.com|(?:www\.)?zenodo\.org|huggingface\.co)/[^\s<>()\]\[\"']+"
)
_RATE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(k?hz)\b", re.IGNORECASE)
_BATCHES = re.compile(
    r"\b(?:containing|all)\s+(\d+)\s+(?:compressed\s+)?(?:packages|batches)\b", re.IGNORECASE
)
_TIME_SERIES_EXTENSIONS = {
    ".csv",
    ".tsv",
    ".mat",
    ".parquet",
    ".h5",
    ".hdf5",
    ".npy",
    ".npz",
}
_ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".zst", ".tar.gz", ".tar.zst"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_NATIVE_DOCUMENTS = 8
_MAX_REDIRECTS = 3


class NativeResponseTooLarge(SourceUnavailable):
    pass


class NativeFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    size: int = Field(ge=0)
    provider_locator: str | None = None
    download_url: str | None = None
    checksum: SourceChecksum | None = None


class NativeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str
    source_kind: SourceKind
    name: str
    revision: str
    license_id: str | None = None
    text: str = ""
    files: list[NativeFile] = Field(default_factory=list)
    related_urls: list[str] = Field(default_factory=list)
    batch_count: int | None = Field(default=None, ge=0)


class VerifiedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: DatasetProfile
    evidence: list[EvidenceRecord]
    documents: list[NativeDocument]


def _extension(name: str) -> str:
    lower = name.casefold()
    for compound in (".tar.zst", ".tar.gz"):
        if lower.endswith(compound):
            return compound
    return Path(lower).suffix


def _plain_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _related_urls(text: str) -> list[str]:
    urls: list[str] = []
    for raw_url in _RELATED_URL.findall(text):
        source = canonical_source_url(raw_url.rstrip(".,;"))
        if source:
            urls.append(source[0])
    return list(dict.fromkeys(urls))


def _describes_dataset_repository(document: NativeDocument) -> bool:
    if document.source_kind is not SourceKind.GITHUB:
        return False
    extensions = {_extension(file.name) for file in document.files}
    text = document.text.casefold()
    return bool(extensions & _TIME_SERIES_EXTENSIONS) or bool(
        "dataset structure" in text and re.search(r"\btime[- ]series\b", text)
    )


def direct_data_files(document: NativeDocument) -> list[NativeFile]:
    supported = _TIME_SERIES_EXTENSIONS | _ARCHIVE_EXTENSIONS
    return [
        file for file in document.files if file.size > 0 and _extension(file.name) in supported
    ]


def _asset_role(file: NativeFile) -> AssetRole | None:
    extension = _extension(file.name)
    if extension in _TIME_SERIES_EXTENSIONS | _ARCHIVE_EXTENSIONS:
        return AssetRole.DATA
    basename = Path(file.name).name.casefold()
    if "checksum" in basename or basename in {"sha256sums", "sha256sums.txt"}:
        return AssetRole.CHECKSUM
    if extension in {".md", ".txt"} and basename.startswith(
        ("readme", "license", "licence", "citation", "dataset_card")
    ):
        return AssetRole.DOCUMENTATION
    return None


def _source_assets(candidate: DatasetCandidate, document: NativeDocument) -> list[SourceAsset]:
    assets: list[SourceAsset] = []
    for file in document.files:
        role = _asset_role(file)
        if role is None or file.size <= 0 or not file.provider_locator or not file.download_url:
            continue
        material = "|".join((candidate.id, file.provider_locator))
        assets.append(
            SourceAsset(
                asset_id=f"asset_{hashlib.sha256(material.encode()).hexdigest()[:16]}",
                name=file.name,
                role=role,
                size_bytes=file.size,
                provider_locator=file.provider_locator,
                download_url=file.download_url,
                source_checksum=file.checksum,
            )
        )
    return assets


def _source_checksum(value: object) -> SourceChecksum | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    algorithm, digest = value.split(":", 1)
    try:
        return SourceChecksum(algorithm=ChecksumAlgorithm(algorithm.casefold()), value=digest)
    except ValueError:
        return None


def _safe_download_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return validate_source_url(value)
    except ValueError:
        return None


def _object_items(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _evidence_id(candidate_id: str, source_url: str, claim: str, value: str) -> str:
    material = "|".join((candidate_id, source_url, claim, value))
    return f"ev_{hashlib.sha256(material.encode()).hexdigest()[:16]}"


def _evidence(
    candidate_id: str,
    document: NativeDocument,
    claim: str,
    value: str,
) -> EvidenceRecord:
    precedence = {
        SourceKind.ZENODO: 100,
        SourceKind.HUGGING_FACE: 90,
        SourceKind.GITHUB: 70,
    }[document.source_kind]
    return EvidenceRecord(
        id=_evidence_id(candidate_id, document.source_url, claim, value),
        candidate_id=candidate_id,
        claim_key=claim,
        observed_value=value,
        source_url=document.source_url,
        source_kind=document.source_kind,
        status=VerificationStatus.VERIFIED,
        precedence=precedence,
    )


def build_verified_candidate(
    candidate: DatasetCandidate,
    documents: list[NativeDocument],
    max_download_bytes: int,
) -> VerifiedCandidate:
    evidence: list[EvidenceRecord] = []
    labels: set[str] = set()
    extensions: set[str] = set()
    sample_rates: list[float] = []
    channel_counts: list[int] = []
    schema_documented = False
    has_native_archive = any(
        document.source_kind is not SourceKind.GITHUB for document in documents
    )

    for document in documents:
        text = document.text.casefold()
        document_extensions = {_extension(file.name) for file in document.files}
        if ".mat" in text or "matlab (.mat)" in text:
            document_extensions.add(".mat")
        extensions.update(document_extensions)
        for label, pattern in {
            "collision": r"\bcollisions?\b",
            "contact": r"\bcontacts?\b",
            "free": r"\b(?:free[- ](?:motion|movement|space)|free from contacts?|fre)\b",
            "anomaly": r"\banomal(?:y|ies|ous)\b",
            "internal mechanical fault": r"\binternal mechanical faults?\b",
        }.items():
            if re.search(pattern, text):
                labels.add(label)
        rate = _RATE.search(text)
        if rate:
            sample_rates.append(
                float(rate.group(1)) * (1_000 if rate.group(2).casefold() == "khz" else 1)
            )
        joint_match = re.search(r"\b(?:all\s+)?(seven|7)\s+joints?\b", text)
        if joint_match:
            channel_counts.append(7)
        document_schema = bool(
            re.search(
                r"\b(?:dataset structure|file structure|seven joints|channels?|columns?)\b", text
            )
            and re.search(r"\b(?:torque|position|velocity|sensor|signal)\b", text)
        )
        schema_documented |= document_schema

        evidence.append(_evidence(candidate.id, document, "revision", document.revision))
        if document.license_id:
            license_claim = (
                "code_license"
                if document.source_kind is SourceKind.GITHUB and has_native_archive
                else "license"
            )
            evidence.append(_evidence(candidate.id, document, license_claim, document.license_id))
        if document_extensions:
            evidence.append(
                _evidence(
                    candidate.id,
                    document,
                    "file_extensions",
                    ",".join(sorted(document_extensions)),
                )
            )
        document_labels = sorted(
            label for label in labels if label in text or label.split()[0] in text
        )
        if document_labels:
            evidence.append(_evidence(candidate.id, document, "labels", ",".join(document_labels)))
        if rate:
            evidence.append(
                _evidence(candidate.id, document, "sample_rate_hz", str(sample_rates[-1]))
            )
        if joint_match:
            evidence.append(_evidence(candidate.id, document, "channel_count", "7"))
        if document_schema:
            evidence.append(_evidence(candidate.id, document, "schema", "documented"))
        batch_count = document.batch_count
        if batch_count is None and (match := _BATCHES.search(document.text)):
            batch_count = int(match.group(1))
        if batch_count is not None:
            if document.source_kind is SourceKind.GITHUB and "each part" in text:
                evidence.append(
                    _evidence(candidate.id, document, "batch_count_part_i", str(batch_count))
                )
                evidence.append(
                    _evidence(candidate.id, document, "batch_count_part_ii", str(batch_count))
                )
            else:
                scope = (
                    "part_ii"
                    if "part ii" in document.name.casefold() or "intentional contact" in text
                    else "part_i"
                    if "part i" in document.name.casefold() or "accidental collision" in text
                    else ""
                )
                claim = f"batch_count_{scope}" if scope else "batch_count"
                evidence.append(_evidence(candidate.id, document, claim, str(batch_count)))
        if document.files:
            evidence.append(
                _evidence(
                    candidate.id,
                    document,
                    "total_size_bytes",
                    str(sum(file.size for file in document.files)),
                )
            )

    primary = next(
        (
            document
            for document in documents
            if document.source_url.rstrip("/")
            == str(candidate.canonical_url).rstrip("/")
        ),
        documents[0],
    )
    preferred = sorted(
        documents,
        key=lambda item: (
            item.source_kind is not SourceKind.ZENODO,
            item.source_kind is not SourceKind.HUGGING_FACE,
        ),
    )
    dataset_documents = [
        document for document in documents if document.source_kind is not SourceKind.GITHUB
    ] or documents
    total_size = sum(sum(file.size for file in document.files) for document in dataset_documents)
    file_count = sum(len(document.files) for document in dataset_documents)
    licence = next((document.license_id for document in preferred if document.license_id), None)
    dataset_licence = next(
        (
            document.license_id
            for document in preferred
            if document.source_kind is not SourceKind.GITHUB and document.license_id
        ),
        primary.license_id if primary.source_kind is not SourceKind.GITHUB else None,
    )
    code_licence = primary.license_id if primary.source_kind is SourceKind.GITHUB else None
    revision = ";".join(
        f"{document.source_kind.value}:{document.revision}" for document in preferred
    )
    has_time_series = bool(extensions & _TIME_SERIES_EXTENSIONS) or (
        bool(extensions & _ARCHIVE_EXTENSIONS)
        and any("time-series" in document.text.casefold() for document in documents)
    )
    profile = DatasetProfile(
        candidate_id=candidate.id,
        name=candidate.name,
        canonical_url=candidate.canonical_url,
        source_kinds=list(dict.fromkeys(document.source_kind for document in preferred)),
        revision=revision or None,
        source_kind=primary.source_kind,
        source_revision=primary.revision,
        assets=_source_assets(candidate, primary),
        license_id=licence,
        dataset_license_id=dataset_licence,
        code_license_id=code_licence,
        file_count=file_count,
        total_size_bytes=total_size,
        file_extensions=sorted(extensions),
        labels=sorted(labels),
        sample_rate_hz=min(sample_rates) if sample_rates else None,
        channel_count=max(channel_counts) if channel_counts else None,
        has_time_series_files=has_time_series,
        schema_documented=schema_documented,
        acquisition_feasible=(
            bool(dataset_documents)
            and all(
                document.files and all(file.size > 0 for file in document.files)
                for document in dataset_documents
            )
            and total_size <= max_download_bytes
        ),
    )
    return VerifiedCandidate(profile=profile, evidence=evidence, documents=documents)


class NativeVerifier:
    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        *,
        validate_dns: bool = True,
    ):
        self.settings = settings
        self.client = client or httpx.Client(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None
        self.validate_dns = validate_dns

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def verify(
        self,
        candidate: DatasetCandidate,
        *,
        cached: bool = False,
        max_download_bytes: int = 25_000_000_000,
    ) -> VerifiedCandidate:
        documents = (
            self._fixture_documents(candidate) if cached else self._live_documents(candidate)
        )
        return build_verified_candidate(
            candidate,
            documents,
            max_download_bytes=max_download_bytes,
        )

    def _fixture_documents(self, candidate: DatasetCandidate) -> list[NativeDocument]:
        fixture_path = Path(__file__).parent.parent / "fixtures" / "robot_collision_native.json"
        documents = [
            NativeDocument.model_validate(item)
            for item in json.loads(fixture_path.read_text(encoding="utf-8"))
        ]
        canonical_url = str(candidate.canonical_url).rstrip("/")
        primary = next(
            (item for item in documents if item.source_url.rstrip("/") == canonical_url),
            None,
        )
        if primary is None:
            raise SourceUnavailable("No cached native fixture exists for this candidate")
        related_urls = (
            set(primary.related_urls) if _describes_dataset_repository(primary) else set()
        )
        return [
            primary,
            *[item for item in documents if item.source_url in related_urls],
        ]

    def _get_bytes(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> bytes | None:
        current_url = url
        original_host = (urlsplit(url).hostname or "").casefold()
        for redirect_count in range(_MAX_REDIRECTS + 1):
            validate_source_url(current_url)
            if self.validate_dns:
                resolve_public_host(urlsplit(current_url).hostname or "")
            with self.client.stream("GET", current_url, headers=headers) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise SourceUnavailable(
                            "Native source returned a redirect without a target"
                        )
                    redirected_url = urljoin(current_url, location)
                    validate_source_url(redirected_url)
                    redirected_host = (urlsplit(redirected_url).hostname or "").casefold()
                    if redirected_host != original_host:
                        raise SourceUnavailable("Native source redirect changed hosts")
                    if redirect_count == _MAX_REDIRECTS:
                        raise SourceUnavailable("Native source exceeded the redirect limit")
                    current_url = redirected_url
                    continue
                if allow_not_found and response.status_code == 404:
                    return None
                response.raise_for_status()
                declared_length = response.headers.get("Content-Length")
                if (
                    declared_length
                    and int(declared_length) > self.settings.max_source_response_bytes
                ):
                    raise NativeResponseTooLarge(
                        "Native source response exceeded the configured size limit"
                    )
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > self.settings.max_source_response_bytes:
                        raise NativeResponseTooLarge(
                            "Native source response exceeded the configured size limit"
                        )
                return bytes(content)
        raise SourceUnavailable("Native source exceeded the redirect limit")

    def _get_json(self, url: str, headers: dict[str, str] | None = None) -> dict:
        content = self._get_bytes(url, headers)
        if content is None:
            raise SourceUnavailable("Required native JSON source was not found")
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise SourceUnavailable("Native source returned an unexpected JSON shape")
        return payload

    def _get_text(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> str | None:
        content = self._get_bytes(url, headers, allow_not_found=allow_not_found)
        return None if content is None else content.decode("utf-8", errors="replace")

    def _live_documents(self, candidate: DatasetCandidate) -> list[NativeDocument]:
        queued = [(str(candidate.canonical_url), 0)]
        documents: list[NativeDocument] = []
        visited: set[str] = set()
        while queued and len(documents) < _MAX_NATIVE_DOCUMENTS:
            url, depth = queued.pop(0)
            source = canonical_source_url(url)
            if not source or source[0] in visited:
                continue
            visited.add(source[0])
            try:
                document = self._fetch_document(*source)
            except (SourceUnavailable, ValueError, OSError, httpx.HTTPError):
                if depth == 0:
                    raise
                continue
            documents.append(document)
            if depth == 0 and _describes_dataset_repository(document):
                queued.extend((item, 1) for item in document.related_urls)
        return documents

    def _fetch_document(self, url: str, kind: SourceKind) -> NativeDocument:
        if kind is SourceKind.GITHUB:
            return self._github(url)
        if kind is SourceKind.ZENODO:
            return self._zenodo(url)
        return self._hugging_face(url)

    def _github(self, url: str) -> NativeDocument:
        owner, repo = urlsplit(url).path.strip("/").split("/")[:2]
        api = f"https://api.github.com/repos/{owner}/{repo}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token.get_secret_value()}"
        metadata = self._get_json(api, headers)
        readme = self._get_text(
            f"{api}/readme",
            headers=headers | {"Accept": "application/vnd.github.raw+json"},
            allow_not_found=True,
        ) or ""
        branch = str(metadata.get("default_branch", "main"))
        commit = self._get_json(f"{api}/commits/{branch}", headers)
        commit_sha = str(commit.get("sha") or "")
        if not commit_sha:
            raise SourceUnavailable("GitHub repository did not expose an immutable commit revision")
        try:
            tree = self._get_json(f"{api}/git/trees/{branch}?recursive=1", headers)
        except NativeResponseTooLarge:
            tree = {}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {404, 409, 422}:
                raise
            tree = {}
        if tree.get("truncated"):
            tree = {}
        files = [
            NativeFile(
                name=str(item.get("path", "")),
                size=int(item.get("size", 0)),
                provider_locator=(
                    f"github:{owner}/{repo}:blob:{item.get('sha')}" if item.get("sha") else None
                ),
                download_url=(
                    f"{api}/git/blobs/{item.get('sha')}" if item.get("sha") else None
                ),
            )
            for item in _object_items(tree.get("tree"))
            if item.get("type") == "blob"
        ]
        licence = metadata.get("license") or {}
        return NativeDocument(
            source_url=url,
            source_kind=SourceKind.GITHUB,
            name=str(metadata.get("full_name", repo)),
            revision=commit_sha,
            license_id=licence.get("spdx_id"),
            text=f"{metadata.get('description') or ''}\n{readme}",
            files=files,
            related_urls=_related_urls(readme),
        )

    def _zenodo(self, url: str) -> NativeDocument:
        record_id = urlsplit(url).path.rstrip("/").split("/")[-1]
        payload = self._get_json(f"https://zenodo.org/api/records/{record_id}")
        record_revision = payload.get("revision")
        if not isinstance(record_revision, int) or record_revision < 1:
            raise SourceUnavailable("Zenodo record did not expose an immutable revision")
        metadata = payload.get("metadata") or {}
        text = _plain_text(str(metadata.get("description", "")))
        files = []
        for item in _object_items(payload.get("files")):
            name = str(item.get("key", ""))
            links = item.get("links") if isinstance(item.get("links"), dict) else {}
            download_url = links.get("self")
            files.append(
                NativeFile(
                    name=name,
                    size=int(item.get("size", 0)),
                    provider_locator=f"zenodo:{record_id}:{name}",
                    download_url=_safe_download_url(download_url),
                    checksum=_source_checksum(item.get("checksum")),
                )
            )
        licence = metadata.get("license") or {}
        batch_files = [file for file in files if "batch-" in file.name.casefold()]
        related = _related_urls(text)
        code_url = (metadata.get("custom") or {}).get("code:codeRepository")
        if isinstance(code_url, str) and canonical_source_url(code_url):
            related.append(canonical_source_url(code_url)[0])
        return NativeDocument(
            source_url=url,
            source_kind=SourceKind.ZENODO,
            name=str(payload.get("title") or metadata.get("title") or record_id),
            revision=f"{record_id}.r{record_revision}",
            license_id=licence.get("id"),
            text=text,
            files=files,
            related_urls=list(dict.fromkeys(related)),
            batch_count=len(batch_files) or None,
        )

    def _hugging_face(self, url: str) -> NativeDocument:
        namespace = "/".join(urlsplit(url).path.strip("/").split("/")[1:3])
        payload = self._get_json(f"https://huggingface.co/api/datasets/{namespace}")
        card = payload.get("cardData") or {}
        siblings = _object_items(payload.get("siblings"))
        files = []
        revision = str(payload.get("sha", ""))
        if not revision:
            raise SourceUnavailable("Hugging Face dataset did not expose an immutable revision")
        for item in siblings:
            lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
            name = str(item.get("rfilename", ""))
            lfs_oid = lfs.get("oid")
            checksum = _source_checksum(f"sha256:{lfs_oid}") if lfs_oid else None
            files.append(
                NativeFile(
                    name=name,
                    size=int(item.get("size") or lfs.get("size") or 0),
                    provider_locator=(
                        f"huggingface:datasets/{namespace}:{revision}:{name}"
                        if revision and name
                        else None
                    ),
                    download_url=(
                        f"https://huggingface.co/datasets/{namespace}/resolve/"
                        f"{quote(revision, safe='')}/{quote(name, safe='/')}"
                        if revision and name
                        else None
                    ),
                    checksum=checksum,
                )
            )
        return NativeDocument(
            source_url=url,
            source_kind=SourceKind.HUGGING_FACE,
            name=str(payload.get("id", namespace)),
            revision=revision,
            license_id=card.get("license"),
            text=str(payload.get("description") or card),
            files=files,
        )
