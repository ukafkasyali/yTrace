"""Human-approved implementation decisions layered over immutable semantic specs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .models import DatasetSpec, EvidenceClaim, EvidenceStatus


HUMAN_CONFIRMATION = "human_confirmation"
_FIELD_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[([0-9]+)\])?")


class ImplementationHandoffError(ValueError):
    """Report an invalid downstream requirement or implementation override."""


@dataclass(frozen=True)
class DownstreamRequirement:
    """Describe one semantic value required by a downstream interface."""

    field_path: str
    downstream_system: str
    requirement: str
    best_supported_candidate: Any = None
    supporting_evidence_refs: tuple[str, ...] = ()
    remaining_uncertainty: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DownstreamRequirement:
        return cls(
            field_path=raw["field_path"],
            downstream_system=raw["downstream_system"],
            requirement=raw["requirement"],
            best_supported_candidate=raw.get("best_supported_candidate"),
            supporting_evidence_refs=tuple(raw.get("supporting_evidence_refs", ())),
            remaining_uncertainty=raw.get("remaining_uncertainty"),
        )


@dataclass(frozen=True)
class BlockingUnresolvedField:
    """Expose an unresolved semantic field that prevents implementation."""

    field_path: str
    semantic_resolution: str
    semantic_value: Any
    downstream_system: str
    requirement: str
    best_supported_candidate: Any
    supporting_evidence_refs: tuple[str, ...]
    remaining_uncertainty: str | None


@dataclass(frozen=True)
class ImplementationOverride:
    """Record an approved downstream value that is not semantic evidence."""

    override_id: str
    field_path: str
    value: Any
    source: str
    approved_by: str
    rationale: str
    downstream_system: str
    evidence_refs: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ImplementationOverride:
        return cls(
            override_id=raw["override_id"],
            field_path=raw["field_path"],
            value=raw.get("value"),
            source=raw.get("source", ""),
            approved_by=raw.get("approved_by", ""),
            rationale=raw.get("rationale", ""),
            downstream_system=raw.get("downstream_system", ""),
            evidence_refs=tuple(raw.get("evidence_refs", ())),
        )


@dataclass(frozen=True)
class ImplementationOverrideArtifact:
    """Bind decisions and requirements to one semantic-spec snapshot."""

    schema_version: str
    dataset_id: str
    semantic_spec_sha256: str
    requirements: tuple[DownstreamRequirement, ...]
    overrides: tuple[ImplementationOverride, ...]
    downstream_context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False) + "\n"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ImplementationOverrideArtifact:
        if raw.get("schema_version") != "1.0":
            raise ImplementationHandoffError(
                "unsupported implementation-override schema_version; expected '1.0'"
            )
        return cls(
            schema_version="1.0",
            dataset_id=raw["dataset_id"],
            semantic_spec_sha256=raw["semantic_spec_sha256"],
            requirements=tuple(
                DownstreamRequirement.from_dict(item)
                for item in raw.get("requirements", ())
            ),
            overrides=tuple(
                ImplementationOverride.from_dict(item)
                for item in raw.get("overrides", ())
            ),
            downstream_context=raw.get("downstream_context"),
        )

    @classmethod
    def read_json(cls, path: str | Path) -> ImplementationOverrideArtifact:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ImplementationHandoffError(
                "implementation-override JSON root must be an object"
            )
        return cls.from_dict(raw)


@dataclass(frozen=True)
class ConnectorHandoff:
    """Connector view with semantic truth and decisions kept separate."""

    schema_version: str
    dataset_id: str
    semantic_spec_sha256: str
    semantic_spec: dict[str, Any]
    blocking_unresolved_fields: tuple[BlockingUnresolvedField, ...]
    implementation_overrides: tuple[ImplementationOverride, ...]
    implementation_view: dict[str, dict[str, Any]]
    downstream_context: dict[str, Any] | None
    connector_ready: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False) + "\n"

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")


def semantic_spec_sha256(spec: DatasetSpec) -> str:
    """Return the stable digest binding decisions to a semantic snapshot."""
    return hashlib.sha256(spec.to_json().encode()).hexdigest()


def detect_blocking_unresolved_fields(
    spec: DatasetSpec,
    requirements: tuple[DownstreamRequirement, ...],
) -> tuple[BlockingUnresolvedField, ...]:
    """Return downstream-required fields whose semantic status is unresolved."""
    blockers = []
    seen: set[str] = set()
    for requirement in requirements:
        _validate_requirement(requirement)
        if requirement.field_path in seen:
            raise ImplementationHandoffError(
                f"duplicate downstream requirement for {requirement.field_path!r}"
            )
        seen.add(requirement.field_path)
        target = _resolve_field(spec, requirement.field_path)
        claim = _resolution_claim(target, requirement.field_path)
        if claim.status is not EvidenceStatus.UNRESOLVED:
            continue
        blockers.append(
            BlockingUnresolvedField(
                field_path=requirement.field_path,
                semantic_resolution=claim.status.value,
                semantic_value=_json_value(target),
                downstream_system=requirement.downstream_system,
                requirement=requirement.requirement,
                best_supported_candidate=requirement.best_supported_candidate,
                supporting_evidence_refs=requirement.supporting_evidence_refs,
                remaining_uncertainty=requirement.remaining_uncertainty,
            )
        )
    return tuple(blockers)


def build_connector_handoff(
    spec: DatasetSpec,
    artifact: ImplementationOverrideArtifact,
    *,
    evidence_ids: set[str] | None = None,
) -> ConnectorHandoff:
    """Validate overrides and build a deterministic connector-facing view."""
    digest = semantic_spec_sha256(spec)
    if artifact.dataset_id != spec.identity.dataset_id:
        raise ImplementationHandoffError(
            f"override dataset_id {artifact.dataset_id!r} does not match "
            f"{spec.identity.dataset_id!r}"
        )
    if artifact.semantic_spec_sha256 != digest:
        raise ImplementationHandoffError(
            "override artifact targets a different semantic-spec snapshot"
        )

    blockers = detect_blocking_unresolved_fields(spec, artifact.requirements)
    if evidence_ids is not None and any(
        reference not in evidence_ids
        for requirement in artifact.requirements
        for reference in requirement.supporting_evidence_refs
    ):
        raise ImplementationHandoffError(
            "downstream requirement references evidence absent from the bounded evidence run"
        )
    requirements = {item.field_path: item for item in artifact.requirements}
    overrides: dict[str, ImplementationOverride] = {}
    override_ids: set[str] = set()
    for override in artifact.overrides:
        if override.field_path in overrides:
            raise ImplementationHandoffError(
                f"duplicate or conflicting overrides for {override.field_path!r}"
            )
        if override.override_id in override_ids:
            raise ImplementationHandoffError(
                f"duplicate implementation override id {override.override_id!r}"
            )
        requirement = requirements.get(override.field_path)
        if requirement is None:
            raise ImplementationHandoffError(
                f"override field {override.field_path!r} has no downstream requirement"
            )
        target = _resolve_field(spec, override.field_path)
        claim = _resolution_claim(target, override.field_path)
        if claim.status is not EvidenceStatus.UNRESOLVED:
            raise ImplementationHandoffError(
                f"field {override.field_path!r} is {claim.status.value}, not unresolved; "
                "an implementation override cannot replace semantic truth"
            )
        _validate_override(override, requirement, evidence_ids)
        overrides[override.field_path] = override
        override_ids.add(override.override_id)

    implementation_view = {
        path: {
            "value": override.value,
            "value_source": override.source,
            "override_id": override.override_id,
            "semantic_resolution": "unresolved",
        }
        for path, override in sorted(overrides.items())
    }
    unresolved_paths = {item.field_path for item in blockers}
    return ConnectorHandoff(
        schema_version="1.0",
        dataset_id=spec.identity.dataset_id,
        semantic_spec_sha256=digest,
        semantic_spec=spec.to_dict(),
        blocking_unresolved_fields=blockers,
        implementation_overrides=tuple(overrides[path] for path in sorted(overrides)),
        implementation_view=implementation_view,
        downstream_context=artifact.downstream_context,
        connector_ready=unresolved_paths <= set(overrides),
    )


def _validate_requirement(requirement: DownstreamRequirement) -> None:
    if not isinstance(requirement.field_path, str) or not requirement.field_path.strip():
        raise ImplementationHandoffError("downstream requirement needs a field_path")
    if (
        not isinstance(requirement.downstream_system, str)
        or not requirement.downstream_system.strip()
    ):
        raise ImplementationHandoffError("downstream requirement needs a downstream_system")
    if not isinstance(requirement.requirement, str) or not requirement.requirement.strip():
        raise ImplementationHandoffError("downstream requirement needs an explanation")
    if any(
        not isinstance(reference, str) or not reference.startswith("ev_")
        for reference in requirement.supporting_evidence_refs
    ):
        raise ImplementationHandoffError(
            "downstream requirement supporting_evidence_refs must be evidence IDs"
        )


def _validate_override(
    override: ImplementationOverride,
    requirement: DownstreamRequirement,
    evidence_ids: set[str] | None,
) -> None:
    if not isinstance(override.override_id, str) or not override.override_id.strip():
        raise ImplementationHandoffError("implementation override needs an override_id")
    if override.source != HUMAN_CONFIRMATION:
        raise ImplementationHandoffError(
            "implementation override source must be explicit human_confirmation"
        )
    if not isinstance(override.approved_by, str) or not override.approved_by.strip():
        raise ImplementationHandoffError("human confirmation must identify approved_by")
    if override.value is None:
        raise ImplementationHandoffError("implementation override value must not be null")
    if not isinstance(override.rationale, str) or not override.rationale.strip():
        raise ImplementationHandoffError("implementation override needs a rationale")
    if override.downstream_system != requirement.downstream_system:
        raise ImplementationHandoffError(
            "implementation override downstream_system does not match its requirement"
        )
    if any(
        not isinstance(reference, str) or not reference.startswith("ev_")
        for reference in override.evidence_refs
    ):
        raise ImplementationHandoffError(
            "implementation override evidence_refs must be evidence IDs"
        )
    if evidence_ids is not None and any(
        reference not in evidence_ids for reference in override.evidence_refs
    ):
        raise ImplementationHandoffError(
            "implementation override references evidence absent from the bounded evidence run"
        )


def _resolve_field(root: Any, path: str) -> Any:
    if not path:
        raise ImplementationHandoffError("field_path must not be empty")
    current = root
    for token in path.split("."):
        match = _FIELD_TOKEN.fullmatch(token)
        if match is None:
            raise ImplementationHandoffError(f"invalid field path {path!r}")
        name, raw_index = match.groups()
        if not hasattr(current, name):
            raise ImplementationHandoffError(f"field path {path!r} does not exist")
        current = getattr(current, name)
        if raw_index is not None:
            index = int(raw_index)
            if not isinstance(current, (tuple, list)) or index >= len(current):
                raise ImplementationHandoffError(f"field path {path!r} does not exist")
            current = current[index]
    return current


def _resolution_claim(target: Any, path: str) -> EvidenceClaim:
    if isinstance(target, EvidenceClaim):
        return target
    claim = getattr(target, "resolution", None)
    if isinstance(claim, EvidenceClaim):
        return claim
    raise ImplementationHandoffError(
        f"field {path!r} has no semantic resolution and is not eligible for override"
    )


def _json_value(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value
