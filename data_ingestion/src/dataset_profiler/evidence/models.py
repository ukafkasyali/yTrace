"""Structured models for the bounded semantic-evidence interface."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import json
from pathlib import Path
from typing import Any


class EvidenceKind(StrEnum):
    DATASET_SUMMARY = "dataset_summary"
    VARIABLE_SCHEMA = "variable_schema"
    SIGNAL_STATISTICS = "signal_statistics"
    METADATA_SUMMARY = "metadata_summary"
    BOUNDED_EXCERPT = "bounded_excerpt"
    DOCUMENTATION_SOURCES = "documentation_sources"
    DOCUMENTATION = "documentation"


class EvidenceErrorCode(StrEnum):
    UNKNOWN_VARIABLE = "UNKNOWN_VARIABLE"
    UNKNOWN_RUN = "UNKNOWN_RUN"
    INVALID_QUERY = "INVALID_QUERY"
    EXCERPT_TOO_LARGE = "EXCERPT_TOO_LARGE"
    CHANNEL_LIMIT_EXCEEDED = "CHANNEL_LIMIT_EXCEEDED"
    QUERY_BUDGET_EXCEEDED = "QUERY_BUDGET_EXCEEDED"
    EXCERPT_BUDGET_EXCEEDED = "EXCERPT_BUDGET_EXCEEDED"
    SAMPLE_BUDGET_EXCEEDED = "SAMPLE_BUDGET_EXCEEDED"
    UNAVAILABLE_EVIDENCE = "UNAVAILABLE_EVIDENCE"
    UNAVAILABLE_DOCUMENTATION = "UNAVAILABLE_DOCUMENTATION"
    UNSUPPORTED_DOCUMENTATION = "UNSUPPORTED_DOCUMENTATION"
    INVALID_DOCUMENTATION_SOURCE = "INVALID_DOCUMENTATION_SOURCE"
    DOCUMENTATION_BUDGET_EXCEEDED = "DOCUMENTATION_BUDGET_EXCEEDED"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"


@dataclass(frozen=True)
class Evidence:
    """One reproducible, machine-readable evidence item."""

    id: str
    kind: EvidenceKind
    source: str
    scope: str
    target: str | None
    value: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    ok: bool = field(default=True, init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False) + "\n"


@dataclass(frozen=True)
class EvidenceError:
    """A normal query failure suitable for direct agent feedback."""

    code: EvidenceErrorCode
    query: EvidenceKind
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    ok: bool = field(default=False, init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False) + "\n"


EvidenceResponse = Evidence | EvidenceError


@dataclass(frozen=True)
class DocumentationSource:
    """A host-registered document; evidence queries never receive its filesystem path."""

    source_id: str
    path: str | Path
    display_name: str | None = None


@dataclass(frozen=True)
class EvidenceLimits:
    """Hard per-query output limits. These are never caller-overridable per request."""

    max_excerpt_samples: int = 32
    max_excerpt_channels: int = 8
    max_statistics_entries: int = 16
    max_metadata_entries: int = 50
    max_response_bytes: int = 32_768
    max_documentation_sources: int = 16
    max_document_bytes: int = 1_048_576
    max_documentation_results: int = 5
    max_documentation_excerpt_chars: int = 800
    max_documentation_response_chars: int = 3_200


@dataclass(frozen=True)
class EvidenceBudget:
    """Cumulative limits for one semantic-agent evidence session."""

    max_total_queries: int = 32
    max_excerpt_queries: int = 4
    max_total_excerpt_values: int = 256
    max_documentation_queries: int = 8
    max_total_documentation_chars: int = 12_000


@dataclass(frozen=True)
class EvidenceUsage:
    total_queries: int
    excerpt_queries: int
    excerpt_values: int
    documentation_queries: int = 0
    documentation_chars: int = 0


DocumentationSearchResponse = list[Evidence] | EvidenceError
