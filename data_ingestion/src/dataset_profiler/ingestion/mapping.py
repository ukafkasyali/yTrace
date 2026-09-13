from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .jobs import IngestionJobStore, ResourceFormat, ResourceProfile


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


class MappingLayout(StrEnum):
    WIDE_TABLE = "WIDE_TABLE"
    LONG_TABLE = "LONG_TABLE"
    NAMED_ARRAYS = "NAMED_ARRAYS"


class MappingValidationError(ValueError):
    pass


class ChannelSpec(_WireModel):
    selector: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=200)
    unit: str | None = Field(default=None, max_length=100)


class MappingSpec(_WireModel):
    schema_version: Literal["1.0"] = "1.0"
    job_revision: int = Field(ge=1)
    resource_id: str = Field(pattern=r"^res_[a-f0-9]{24}$")
    resource_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    layout: MappingLayout
    record_selector: str | None = Field(default=None, max_length=500)
    time_selector: str = Field(min_length=1, max_length=500)
    channel_selector: str | None = Field(default=None, max_length=500)
    value_selector: str | None = Field(default=None, max_length=500)
    signal_selector: str | None = Field(default=None, max_length=500)
    sample_axis: int | None = Field(default=None, ge=0, le=8)
    channel_axis: int | None = Field(default=None, ge=0, le=8)
    channels: list[ChannelSpec] = Field(min_length=1, max_length=10_000)
    annotations: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def layout_fields_match(self) -> MappingSpec:
        if len({channel.selector for channel in self.channels}) != len(self.channels):
            raise ValueError("channel selectors must be unique")
        if len({channel.name for channel in self.channels}) != len(self.channels):
            raise ValueError("channel names must be unique")
        if self.layout is MappingLayout.LONG_TABLE:
            if not self.channel_selector or not self.value_selector or self.signal_selector:
                raise ValueError("long mappings require channel and value selectors")
        elif self.layout is MappingLayout.NAMED_ARRAYS:
            if (
                not self.signal_selector
                or self.sample_axis is None
                or self.channel_axis is None
                or self.sample_axis == self.channel_axis
            ):
                raise ValueError("array mappings require distinct sample and channel axes")
        elif self.channel_selector or self.value_selector or self.signal_selector:
            raise ValueError("wide mappings use channel selectors directly")
        return self

    def require_confirmation_fields(self) -> None:
        if any(not channel.unit for channel in self.channels):
            raise MappingValidationError("Every mapped channel requires an explicit unit")


class MappingProposal(_WireModel):
    resource_id: str
    candidates: list[MappingSpec]
    issues: list[str]
    requires_confirmation: bool = True


class ConfirmedMapping(_WireModel):
    mapping_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping: MappingSpec
    created: bool


class MappingService:
    TIME_NAMES: ClassVar[tuple[str, ...]] = ("time", "timestamp", "time_seconds", "seconds", "t")
    RECORD_NAMES: ClassVar[tuple[str, ...]] = (
        "record",
        "record_id",
        "run",
        "run_id",
        "sequence",
        "sequence_id",
    )
    CHANNEL_NAMES: ClassVar[tuple[str, ...]] = (
        "channel",
        "channel_id",
        "signal",
        "signal_id",
    )
    VALUE_NAMES: ClassVar[tuple[str, ...]] = ("value", "measurement", "reading")

    def __init__(self, jobs: IngestionJobStore):
        self.jobs = jobs

    def proposals(self, ingestion_id: str) -> list[MappingProposal]:
        job = self.jobs.get(ingestion_id)
        return [self._proposal(job.job_revision, profile) for profile in self.jobs.list_resources(ingestion_id)]

    def confirm(
        self,
        ingestion_id: str,
        mapping: MappingSpec,
        *,
        require_units: bool = True,
    ) -> ConfirmedMapping:
        if require_units:
            mapping.require_confirmation_fields()
        profile = next(
            (
                item
                for item in self.jobs.list_resources(ingestion_id)
                if item.resource_id == mapping.resource_id
                and item.content_sha256 == mapping.resource_sha256
            ),
            None,
        )
        if profile is not None:
            self._validate_against_profile(mapping, profile)
        payload = mapping.model_dump(mode="json", by_alias=True)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        persisted, created = self.jobs.confirm_mapping(
            ingestion_id=ingestion_id,
            expected_job_revision=mapping.job_revision,
            resource_id=mapping.resource_id,
            resource_sha256=mapping.resource_sha256,
            mapping_sha256=digest,
            mapping_payload=payload,
        )
        return ConfirmedMapping(
            mapping_sha256=digest,
            mapping=MappingSpec.model_validate(persisted),
            created=created,
        )

    @staticmethod
    def _validate_against_profile(mapping: MappingSpec, profile: ResourceProfile) -> None:
        if mapping.layout in {MappingLayout.WIDE_TABLE, MappingLayout.LONG_TABLE}:
            columns = {column["name"] for column in profile.details.get("columns", [])}
            required = {mapping.time_selector, *(mapping.annotations)}
            if mapping.record_selector:
                required.add(mapping.record_selector)
            if mapping.layout is MappingLayout.WIDE_TABLE:
                required.update(channel.selector for channel in mapping.channels)
            else:
                required.update({mapping.channel_selector, mapping.value_selector})
                if any(channel.selector == "*" for channel in mapping.channels):
                    raise MappingValidationError(
                        "Long-table channel values must be selected explicitly"
                    )
            if None in required or not required.issubset(columns):
                raise MappingValidationError("Mapping selector is absent from the tabular schema")
            return
        arrays = {
            array["name"]: array for array in profile.details.get("arrays", [])
        }
        required_arrays = {mapping.time_selector, mapping.signal_selector, *mapping.annotations}
        if None in required_arrays or not required_arrays.issubset(arrays):
            raise MappingValidationError("Mapping selector is absent from the array schema")
        signal_shape = arrays[mapping.signal_selector]["shape"]
        if max(mapping.sample_axis, mapping.channel_axis) >= len(signal_shape):
            raise MappingValidationError("Mapped array axis is outside the signal shape")
        if signal_shape[mapping.channel_axis] != len(mapping.channels):
            raise MappingValidationError("Mapped channel count differs from the signal array")
        time_shape = arrays[mapping.time_selector]["shape"]
        if len(time_shape) != 1 or time_shape[0] != signal_shape[mapping.sample_axis]:
            raise MappingValidationError("Mapped time array does not match the sample axis")

    def get(self, ingestion_id: str) -> ConfirmedMapping | None:
        payload = self.jobs.get_mapping_payload(ingestion_id)
        if payload is None:
            return None
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return ConfirmedMapping(
            mapping_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
            mapping=MappingSpec.model_validate(payload),
            created=False,
        )

    def _proposal(self, job_revision: int, profile: ResourceProfile) -> MappingProposal:
        if profile.format is ResourceFormat.UNSUPPORTED:
            return MappingProposal(
                resource_id=profile.resource_id,
                candidates=[],
                issues=[profile.details.get("reason", "UNSUPPORTED_FORMAT")],
            )
        if profile.format in {ResourceFormat.CSV, ResourceFormat.TSV, ResourceFormat.PARQUET}:
            return self._table_proposal(job_revision, profile)
        return self._array_proposal(job_revision, profile)

    def _table_proposal(self, job_revision: int, profile: ResourceProfile) -> MappingProposal:
        columns = profile.details.get("columns", [])
        names = [column["name"] for column in columns]
        lower = {name.casefold(): name for name in names}
        time_matches = [lower[name] for name in self.TIME_NAMES if name in lower]
        record_matches = [lower[name] for name in self.RECORD_NAMES if name in lower]
        channel_matches = [lower[name] for name in self.CHANNEL_NAMES if name in lower]
        value_matches = [lower[name] for name in self.VALUE_NAMES if name in lower]
        issues: list[str] = []
        if len(time_matches) != 1:
            issues.append("TIME_SELECTOR_AMBIGUOUS")
            return MappingProposal(resource_id=profile.resource_id, candidates=[], issues=issues)
        if len(record_matches) > 1:
            issues.append("RECORD_SELECTOR_AMBIGUOUS")
            return MappingProposal(resource_id=profile.resource_id, candidates=[], issues=issues)
        if len(channel_matches) > 1 or len(value_matches) > 1:
            issues.append("LONG_LAYOUT_AMBIGUOUS")
            return MappingProposal(resource_id=profile.resource_id, candidates=[], issues=issues)
        time = time_matches[0]
        record = record_matches[0] if record_matches else None
        channel = channel_matches[0] if channel_matches else None
        value = value_matches[0] if value_matches else None
        if channel and value:
            candidates = [
                MappingSpec(
                    job_revision=job_revision,
                    resource_id=profile.resource_id,
                    resource_sha256=profile.content_sha256,
                    layout=MappingLayout.LONG_TABLE,
                    record_selector=record,
                    time_selector=time,
                    channel_selector=channel,
                    value_selector=value,
                    channels=[ChannelSpec(selector="*", name="selected_channel", unit=None)],
                )
            ]
        else:
            numeric = {
                column["name"]
                for column in columns
                if column.get("dtype") not in {"string", "unknown", "bool"}
            }
            selected = [name for name in names if name in numeric and name not in {time, record}]
            candidates = (
                [
                    MappingSpec(
                        job_revision=job_revision,
                        resource_id=profile.resource_id,
                        resource_sha256=profile.content_sha256,
                        layout=MappingLayout.WIDE_TABLE,
                        record_selector=record,
                        time_selector=time,
                        channels=[ChannelSpec(selector=name, name=name, unit=None) for name in selected],
                    )
                ]
                if selected
                else []
            )
        issues.append("CHANNEL_UNITS_REQUIRE_CONFIRMATION")
        return MappingProposal(resource_id=profile.resource_id, candidates=candidates, issues=issues)

    def _array_proposal(self, job_revision: int, profile: ResourceProfile) -> MappingProposal:
        arrays = profile.details.get("arrays", [])
        signals = [array for array in arrays if len(array.get("shape", [])) == 2]
        times = [
            array
            for array in arrays
            if len(array.get("shape", [])) == 1 and array.get("name", "").casefold() in self.TIME_NAMES
        ]
        if len(signals) != 1 or len(times) != 1:
            return MappingProposal(
                resource_id=profile.resource_id,
                candidates=[],
                issues=["ARRAY_LAYOUT_AMBIGUOUS"],
            )
        signal = signals[0]
        time = times[0]
        matches = [index for index, size in enumerate(signal["shape"]) if size == time["shape"][0]]
        if len(matches) != 1:
            return MappingProposal(
                resource_id=profile.resource_id,
                candidates=[],
                issues=["ARRAY_AXES_AMBIGUOUS"],
            )
        sample_axis = matches[0]
        channel_axis = 1 - sample_axis
        channels = [
            ChannelSpec(selector=str(index), name=f"channel_{index + 1}", unit=None)
            for index in range(signal["shape"][channel_axis])
        ]
        mapping = MappingSpec(
            job_revision=job_revision,
            resource_id=profile.resource_id,
            resource_sha256=profile.content_sha256,
            layout=MappingLayout.NAMED_ARRAYS,
            time_selector=time["name"],
            signal_selector=signal["name"],
            sample_axis=sample_axis,
            channel_axis=channel_axis,
            channels=channels,
        )
        return MappingProposal(
            resource_id=profile.resource_id,
            candidates=[mapping],
            issues=["CHANNEL_UNITS_REQUIRE_CONFIRMATION"],
        )
