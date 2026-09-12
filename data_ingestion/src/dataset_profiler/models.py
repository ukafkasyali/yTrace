from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json


@dataclass
class Inference:
    value: Any
    confidence: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class ChannelStats:
    index: int
    count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    standard_deviation: float | None
    nan_count: int
    inf_count: int
    constant: bool
    near_constant: bool


@dataclass
class VariableProfile:
    name: str
    python_type: str
    shape: list[int]
    dtype: str
    ndim: int
    size: int
    minimum: float | None = None
    maximum: float | None = None
    nan_count: int = 0
    inf_count: int = 0
    first_values: list[Any] = field(default_factory=list)
    channel_stats: list[ChannelStats] = field(default_factory=list)
    content_sha256: str | None = None
    nested_structure: Any = None


@dataclass
class FileProfile:
    relative_path: str
    source_run_id: str
    format: str
    size_bytes: int
    sha256: str
    variables: list[VariableProfile]
    loader: str


@dataclass
class Event:
    event_id: str
    observed_index: int
    inferred_time_seconds: float | None
    interpretation: Inference


@dataclass
class RunProfile:
    internal_id: str
    dataset_id: str
    source_run_id: str
    original_sequence_id: str
    source_directory: str
    source_files: list[str]
    sequence_length: int | None
    channel_counts: dict[str, int]
    sampling_rate_hz: float | None
    time_start: float | None
    time_end: float | None
    timestamps_monotonic: bool | None
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)


@dataclass
class AuditIssue:
    severity: str
    check: str
    message: str
    affected_runs: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditSummary:
    checks_run: list[str]
    issues: list[AuditIssue]

    @property
    def counts_by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        return counts

    @property
    def checks_without_issues(self) -> list[str]:
        checks_with_issues = {issue.check for issue in self.issues}
        return [check for check in self.checks_run if check not in checks_with_issues]


@dataclass
class DatasetProfile:
    schema_version: str
    dataset_id: str
    source: dict[str, Any]
    discovery: dict[str, Any]
    files: list[FileProfile]
    runs: list[RunProfile]
    observed_structure: dict[str, Any]
    signals: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    metadata: dict[str, Any]
    quality: AuditSummary
    inferred_semantics: list[dict[str, Any]]
    unknowns: list[str]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["quality"]["counts_by_severity"] = self.quality.counts_by_severity
        result["quality"]["checks_without_issues"] = self.quality.checks_without_issues
        return result

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False)

    def write_json(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.to_json() + "\n", encoding="utf-8")
