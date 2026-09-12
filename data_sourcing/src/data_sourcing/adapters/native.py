from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from data_sourcing.adapters.canonicalize import canonical_source_url
from data_sourcing.adapters.discovery import SourceUnavailable
from data_sourcing.config import Settings
from data_sourcing.models import (
    DatasetCandidate,
    DatasetProfile,
    EvidenceRecord,
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
_TIME_SERIES_EXTENSIONS = {".csv", ".mat", ".parquet", ".h5", ".hdf5", ".npy", ".npz"}
_ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".zst"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_NATIVE_DOCUMENTS = 8
_MAX_REDIRECTS = 3


class NativeResponseTooLarge(SourceUnavailable):
    pass


class NativeFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    size: int = Field(ge=0)


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
        license_id=licence,
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
    return VerifiedCandidate(profile=profile, evidence=evidence)


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
        target = "https://github.com/zhang-zengjie/robot-raw-collision-signals"
        urls = {str(candidate.canonical_url), *(str(url) for url in candidate.related_urls)}
        if target not in urls:
            raise SourceUnavailable("No cached native fixture exists for this candidate")
        fixture_path = Path(__file__).parent.parent / "fixtures" / "robot_collision_native.json"
        return [
            NativeDocument.model_validate(item)
            for item in json.loads(fixture_path.read_text(encoding="utf-8"))
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
        queued = [
            (str(candidate.canonical_url), 0),
            *((str(url), 1) for url in candidate.related_urls),
        ]
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
            NativeFile(name=str(item.get("path", "")), size=int(item.get("size", 0)))
            for item in tree.get("tree", [])
            if item.get("type") == "blob"
        ]
        licence = metadata.get("license") or {}
        return NativeDocument(
            source_url=url,
            source_kind=SourceKind.GITHUB,
            name=str(metadata.get("full_name", repo)),
            revision=str(commit.get("sha", "")),
            license_id=licence.get("spdx_id"),
            text=f"{metadata.get('description') or ''}\n{readme}",
            files=files,
            related_urls=_related_urls(readme),
        )

    def _zenodo(self, url: str) -> NativeDocument:
        record_id = urlsplit(url).path.rstrip("/").split("/")[-1]
        payload = self._get_json(f"https://zenodo.org/api/records/{record_id}")
        metadata = payload.get("metadata") or {}
        text = _plain_text(str(metadata.get("description", "")))
        files = [
            NativeFile(name=str(item.get("key", "")), size=int(item.get("size", 0)))
            for item in payload.get("files", [])
        ]
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
            revision=f"{record_id}.r{payload.get('revision', 0)}",
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
        siblings = payload.get("siblings") or []
        files = []
        for item in siblings:
            lfs = item.get("lfs") or {}
            files.append(
                NativeFile(
                    name=str(item.get("rfilename", "")),
                    size=int(item.get("size") or lfs.get("size") or 0),
                )
            )
        return NativeDocument(
            source_url=url,
            source_kind=SourceKind.HUGGING_FACE,
            name=str(payload.get("id", namespace)),
            revision=str(payload.get("sha", "")),
            license_id=card.get("license"),
            text=str(payload.get("description") or card),
            files=files,
        )
