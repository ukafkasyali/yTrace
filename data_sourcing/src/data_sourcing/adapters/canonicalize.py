from __future__ import annotations

import hashlib
import re
from collections import defaultdict
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


def canonicalize_results(results: list[SearchResult], limit: int = 8) -> list[DatasetCandidate]:
    recognized: list[tuple[SearchResult, str, SourceKind, set[str]]] = []
    for result in results:
        source = canonical_source_url(str(result.url))
        if not source:
            continue
        linked = {
            linked_source[0]
            for match in _URL_PATTERN.findall(result.content)
            if (linked_source := canonical_source_url(match))
        }
        recognized.append((result, source[0], source[1], linked | {source[0]}))

    # Connected components group a code repository with native dataset records it cites.
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for _, canonical_url, _, linked_urls in recognized:
        for linked_url in linked_urls:
            union(canonical_url, linked_url)

    grouped: dict[str, list[tuple[SearchResult, str, SourceKind, set[str]]]] = defaultdict(list)
    for item in recognized:
        grouped[find(item[1])].append(item)

    candidates: list[DatasetCandidate] = []
    for items in grouped.values():
        urls = sorted({url for item in items for url in item[3]})
        kinds = {url: canonical_source_url(url)[1] for url in urls if canonical_source_url(url)}
        github_urls = [url for url in urls if kinds[url] is SourceKind.GITHUB]
        canonical_url = github_urls[0] if github_urls else urls[0]
        source_kind = kinds[canonical_url]
        best = max(items, key=lambda item: item[0].score)
        description = "\n\n".join(
            dict.fromkeys(item[0].content for item in items if item[0].content)
        )
        candidates.append(
            DatasetCandidate(
                id=_candidate_id(canonical_url),
                name=best[0].title,
                canonical_url=canonical_url,
                source_kind=source_kind,
                description=description[:8_000],
                related_urls=[url for url in urls if url != canonical_url],
            )
        )
    return sorted(
        candidates, key=lambda item: (item.source_kind is not SourceKind.ZENODO, item.name)
    )[:limit]
