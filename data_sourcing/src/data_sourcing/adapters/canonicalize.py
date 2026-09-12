from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from data_sourcing.models import DatasetCandidate, SearchResult, SourceKind

_URL_PATTERN = re.compile(r"https://[^\s<>()\]\[\"']+")


def canonical_source_url(raw_url: str) -> tuple[str, SourceKind] | None:
    parsed = urlsplit(raw_url.rstrip(".,;"))
    host = (parsed.hostname or "").casefold()
    segments = [part for part in parsed.path.split("/") if part]
    if host in {"github.com", "www.github.com"} and len(segments) >= 2:
        return (
            f"https://github.com/{segments[0]}/{segments[1].removesuffix('.git')}",
            SourceKind.GITHUB,
        )
    if (
        host in {"zenodo.org", "www.zenodo.org"}
        and len(segments) >= 2
        and segments[0] in {"record", "records"}
        and segments[1].isdigit()
    ):
        return f"https://zenodo.org/records/{segments[1]}", SourceKind.ZENODO
    if host == "huggingface.co" and len(segments) >= 3 and segments[0] == "datasets":
        return (
            f"https://huggingface.co/datasets/{segments[1]}/{segments[2]}",
            SourceKind.HUGGING_FACE,
        )
    return None


def _candidate_id(canonical_url: str) -> str:
    return f"ds_{hashlib.sha256(canonical_url.encode()).hexdigest()[:12]}"


def candidate_from_source(
    raw_url: str,
    *,
    name: str,
    description: str = "",
    discovery_depth: int = 0,
    discovered_from_candidate_id: str | None = None,
) -> DatasetCandidate | None:
    source = canonical_source_url(raw_url)
    if not source:
        return None
    canonical_url, source_kind = source
    return DatasetCandidate(
        id=_candidate_id(canonical_url),
        name=name,
        canonical_url=canonical_url,
        source_kind=source_kind,
        description=description,
        discovery_depth=discovery_depth,
        discovered_from_candidate_id=discovered_from_candidate_id,
    )


def canonicalize_results(results: list[SearchResult], limit: int = 8) -> list[DatasetCandidate]:
    ordered_results = sorted(results, key=lambda item: item.score, reverse=True)
    primary: list[tuple[SearchResult, str, SourceKind, list[str]]] = []
    linked_sources: list[tuple[SearchResult, str, SourceKind]] = []
    for result in ordered_results:
        source = canonical_source_url(str(result.url))
        if not source:
            continue
        linked = list(
            dict.fromkeys(
                linked_source[0]
                for match in _URL_PATTERN.findall(result.content)
                if (linked_source := canonical_source_url(match))
                and linked_source[0] != source[0]
            )
        )
        primary.append((result, source[0], source[1], linked))
        linked_sources.extend(
            (result, linked_source[0], linked_source[1])
            for match in _URL_PATTERN.findall(result.content)
            if (linked_source := canonical_source_url(match))
            and linked_source[0] != source[0]
        )

    candidates: list[DatasetCandidate] = []
    seen: set[str] = set()
    for result, canonical_url, source_kind, related_urls in primary:
        if canonical_url in seen:
            continue
        seen.add(canonical_url)
        candidates.append(
            DatasetCandidate(
                id=_candidate_id(canonical_url),
                name=result.title,
                canonical_url=canonical_url,
                source_kind=source_kind,
                description=result.content,
                related_urls=related_urls,
            )
        )
    for result, canonical_url, source_kind in linked_sources:
        if canonical_url in seen:
            continue
        seen.add(canonical_url)
        candidates.append(
            DatasetCandidate(
                id=_candidate_id(canonical_url),
                name=f"Source linked from {result.title}",
                canonical_url=canonical_url,
                source_kind=source_kind,
                description=result.content,
            )
        )
    return candidates[:limit]
