"""Typed, declarative DatasetSpec v0.1 models and JSON serialization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
import json
from pathlib import Path
from typing import Any


class EvidenceStatus(StrEnum):
    OBSERVED = "observed"
    DOCUMENTED = "documented"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class VariableRole(StrEnum):
    SIGNAL = "signal"
    TIME = "time"
    EVENT = "event"
    METADATA = "metadata"


class TimeAxisKind(StrEnum):
    REGULAR = "regular"
    IRREGULAR = "irregular"


class IndexBase(StrEnum):
    ZERO_BASED = "zero_based"
    MATLAB_ONE_BASED = "matlab_one_based"


class IndexConversion(StrEnum):
    IDENTITY = "identity"
    SUBTRACT_ONE = "subtract_one"


class TimestampRule(StrEnum):
    LOOKUP_TIME_AXIS = "lookup_time_axis"


class EventMappingType(StrEnum):
    TIMEF_POINT_ANNOTATION = "timef_point_annotation"


class ProvenanceScope(StrEnum):
    RECORD = "record"
    SERIES = "series"


@dataclass(frozen=True)
class EvidenceClaim:
    status: EvidenceStatus
    confidence: Confidence | None = None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class DatasetIdentity:
    dataset_id: str
    source_subsets: tuple[str, ...]
    compatible_profile_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecordDiscovery:
    record_unit: str
    boundary: str
    included_run_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceVariable:
    name: str
    role: VariableRole


@dataclass(frozen=True)
class ChannelMapping:
    count: int
    source_indices: tuple[int, ...]
    target_names: tuple[str, ...]


@dataclass(frozen=True)
class UnitClaim:
    name: str | None
    symbol: str | None
    resolution: EvidenceClaim


@dataclass(frozen=True)
class SamplingClaim:
    rate_hz: float | None
    time_axis: str


@dataclass(frozen=True)
class SignalMapping:
    source_variable: str
    semantic_type: str
    channels: ChannelMapping
    dtype: str
    unit: UnitClaim
    sampling: SamplingClaim
    semantics: EvidenceClaim


@dataclass(frozen=True)
class TimeAxisRule:
    name: str
    source_variable: str
    kind: TimeAxisKind
    unit: str
    monotonic: bool
    embedded_signal_row: int | None = None


@dataclass(frozen=True)
class EventMapping:
    source_variable: str
    semantic_type: str
    source_index_base: IndexBase
    index_conversion: IndexConversion
    time_axis: str
    timestamp_rule: TimestampRule
    mapping_type: EventMappingType
    preserve_fields: tuple[str, ...]
    semantics: EvidenceClaim


@dataclass(frozen=True)
class ProvenanceRequirement:
    field: str
    scope: ProvenanceScope
    required: bool = True


@dataclass(frozen=True)
class MLTask:
    name: str
    task_type: str
    target: str | None = None


@dataclass(frozen=True)
class RecordDefaults:
    subject_ids: tuple[str, ...] = ()
    start_time: str | None = None


@dataclass(frozen=True)
class DatasetSpec:
    schema_version: str
    identity: DatasetIdentity
    record_discovery: RecordDiscovery
    source_variables: tuple[SourceVariable, ...]
    signals: tuple[SignalMapping, ...]
    time_axes: tuple[TimeAxisRule, ...]
    events: tuple[EventMapping, ...]
    provenance: tuple[ProvenanceRequirement, ...]
    tasks: tuple[MLTask, ...] = ()
    record_defaults: RecordDefaults = field(default_factory=RecordDefaults)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation with deterministic field order."""
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False) + "\n"

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_json(cls, value: str) -> DatasetSpec:
        raw = json.loads(value)
        if not isinstance(raw, dict):
            raise ValueError("DatasetSpec JSON root must be an object")
        return cls.from_dict(raw)

    @classmethod
    def read_json(cls, path: str | Path) -> DatasetSpec:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DatasetSpec:
        """Decode the v0.1 wire representation into typed models."""
        if raw.get("schema_version") != "0.1":
            raise ValueError("Unsupported DatasetSpec schema_version; expected '0.1'")

        def evidence(value: dict[str, Any]) -> EvidenceClaim:
            return EvidenceClaim(
                status=EvidenceStatus(value["status"]),
                confidence=Confidence(value["confidence"]) if value.get("confidence") else None,
                evidence=tuple(value.get("evidence", ())),
            )

        identity = raw["identity"]
        discovery = raw["record_discovery"]
        defaults = raw.get("record_defaults", {})
        return cls(
            schema_version="0.1",
            identity=DatasetIdentity(
                dataset_id=identity["dataset_id"],
                source_subsets=tuple(identity["source_subsets"]),
                compatible_profile_ids=tuple(identity.get("compatible_profile_ids", ())),
            ),
            record_discovery=RecordDiscovery(
                record_unit=discovery["record_unit"],
                boundary=discovery["boundary"],
                included_run_ids=tuple(discovery.get("included_run_ids", ())),
            ),
            source_variables=tuple(
                SourceVariable(name=item["name"], role=VariableRole(item["role"]))
                for item in raw["source_variables"]
            ),
            signals=tuple(
                SignalMapping(
                    source_variable=item["source_variable"],
                    semantic_type=item["semantic_type"],
                    channels=ChannelMapping(
                        count=item["channels"]["count"],
                        source_indices=tuple(item["channels"]["source_indices"]),
                        target_names=tuple(item["channels"]["target_names"]),
                    ),
                    dtype=item["dtype"],
                    unit=UnitClaim(
                        name=item["unit"].get("name"),
                        symbol=item["unit"].get("symbol"),
                        resolution=evidence(item["unit"]["resolution"]),
                    ),
                    sampling=SamplingClaim(
                        rate_hz=item["sampling"].get("rate_hz"),
                        time_axis=item["sampling"]["time_axis"],
                    ),
                    semantics=evidence(item["semantics"]),
                )
                for item in raw["signals"]
            ),
            time_axes=tuple(
                TimeAxisRule(
                    name=item["name"],
                    source_variable=item["source_variable"],
                    kind=TimeAxisKind(item["kind"]),
                    unit=item["unit"],
                    monotonic=item["monotonic"],
                    embedded_signal_row=item.get("embedded_signal_row"),
                )
                for item in raw["time_axes"]
            ),
            events=tuple(
                EventMapping(
                    source_variable=item["source_variable"],
                    semantic_type=item["semantic_type"],
                    source_index_base=IndexBase(item["source_index_base"]),
                    index_conversion=IndexConversion(item["index_conversion"]),
                    time_axis=item["time_axis"],
                    timestamp_rule=TimestampRule(item["timestamp_rule"]),
                    mapping_type=EventMappingType(item["mapping_type"]),
                    preserve_fields=tuple(item["preserve_fields"]),
                    semantics=evidence(item["semantics"]),
                )
                for item in raw["events"]
            ),
            provenance=tuple(
                ProvenanceRequirement(
                    field=item["field"],
                    scope=ProvenanceScope(item["scope"]),
                    required=item.get("required", True),
                )
                for item in raw["provenance"]
            ),
            tasks=tuple(
                MLTask(name=item["name"], task_type=item["task_type"], target=item.get("target"))
                for item in raw.get("tasks", ())
            ),
            record_defaults=RecordDefaults(
                subject_ids=tuple(defaults.get("subject_ids", ())),
                start_time=defaults.get("start_time"),
            ),
        )
