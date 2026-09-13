from dataclasses import replace
from pathlib import Path

import pytest

from dataset_profiler.semantic_agent.profile_io import read_dataset_profile
from dataset_profiler.semantic_spec import (
    DatasetSpec,
    DownstreamRequirement,
    ImplementationHandoffError,
    ImplementationOverride,
    ImplementationOverrideArtifact,
    build_connector_handoff,
    detect_blocking_unresolved_fields,
    load_kuka_collision_part1_spec,
    semantic_spec_sha256,
    validate_dataset_spec,
)
from dataset_profiler.semantic_spec.models import Confidence, EvidenceClaim, EvidenceStatus


_ROOT = Path(__file__).parents[1]
_BOSCH_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "bosch_unresolved_spec.json"
_KUKA_PROFILE_PATH = _ROOT / "outputs/dataset_profile.json"
_CISS_ID = "ev_documentation_ciss_58517c77b913"
_MG_ID = "ev_documentation_mg_bd382bd44290"
_LOADER_ID = "ev_documentation_not_rescaled_32bf9d638e4e"
_TRANSFORMATION_ID = "ev_documentation_transformation_509e1e2fa633"
_EVIDENCE_IDS = {_CISS_ID, _MG_ID, _LOADER_ID, _TRANSFORMATION_ID}


def _bosch_spec() -> DatasetSpec:
    """Build the unresolved Bosch contract without depending on ignored run outputs."""
    return DatasetSpec.read_json(_BOSCH_FIXTURE_PATH)


def _requirement(field_path: str = "signals[0].unit") -> DownstreamRequirement:
    return DownstreamRequirement(
        field_path=field_path,
        downstream_system="TimeF TimeSeriesSpec.unit_value",
        requirement="TimeF requires a concrete Pint unit.",
        best_supported_candidate={
            "name": "milligravity",
            "symbol": "mg",
            "timef_unit": "milligravity",
        },
        supporting_evidence_refs=tuple(sorted(_EVIDENCE_IDS)),
        remaining_uncertainty="The acquisition-to-HDF5 transformation is undocumented.",
    )


def _override(**changes) -> ImplementationOverride:
    values = {
        "override_id": "bosch-cnc-unit-mg-human-approval-v1",
        "field_path": "signals[0].unit",
        "value": {
            "name": "milligravity",
            "symbol": "mg",
            "timef_unit": "milligravity",
        },
        "source": "human_confirmation",
        "approved_by": "user",
        "rationale": "Use the approved candidate without changing semantic truth.",
        "downstream_system": "TimeF TimeSeriesSpec.unit_value",
        "evidence_refs": tuple(sorted(_EVIDENCE_IDS)),
    }
    values.update(changes)
    return ImplementationOverride(**values)


def _artifact(
    spec: DatasetSpec,
    *overrides: ImplementationOverride,
    requirements: tuple[DownstreamRequirement, ...] | None = None,
) -> ImplementationOverrideArtifact:
    return ImplementationOverrideArtifact(
        schema_version="1.0",
        dataset_id=spec.identity.dataset_id,
        semantic_spec_sha256=semantic_spec_sha256(spec),
        requirements=requirements or (_requirement(),),
        overrides=overrides,
        downstream_context={"dataset_id": "boschresearch/cnc-machining"},
    )


def test_unresolved_field_can_receive_explicit_human_override():
    spec = _bosch_spec()

    handoff = build_connector_handoff(
        spec, _artifact(spec, _override()), evidence_ids=_EVIDENCE_IDS
    )

    assert handoff.connector_ready is True
    assert handoff.implementation_view["signals[0].unit"]["value"]["symbol"] == "mg"
    assert handoff.implementation_view["signals[0].unit"]["value_source"] == (
        "human_confirmation"
    )


def test_override_does_not_mutate_dataset_spec():
    spec = _bosch_spec()
    before = spec.to_json()

    handoff = build_connector_handoff(spec, _artifact(spec, _override()))

    assert spec.to_json() == before
    assert handoff.semantic_spec == spec.to_dict()
    assert spec.signals[0].unit.resolution.status is EvidenceStatus.UNRESOLVED
    assert spec.signals[0].unit.name is None
    assert spec.signals[0].unit.symbol is None


def test_documented_field_cannot_be_silently_overridden():
    spec = _bosch_spec()
    documented = replace(
        spec.signals[0].unit,
        name="acceleration",
        symbol="m/s^2",
        resolution=EvidenceClaim(
            EvidenceStatus.DOCUMENTED,
            Confidence.HIGH,
            ("ev_documentation_unit_existing",),
        ),
    )
    spec = replace(spec, signals=(replace(spec.signals[0], unit=documented),))

    with pytest.raises(ImplementationHandoffError, match="not unresolved"):
        build_connector_handoff(spec, _artifact(spec, _override()))


def test_duplicate_and_conflicting_overrides_are_rejected():
    spec = _bosch_spec()

    with pytest.raises(ImplementationHandoffError, match="duplicate or conflicting"):
        build_connector_handoff(spec, _artifact(spec, _override(), _override()))


def test_missing_human_confirmation_is_rejected():
    spec = _bosch_spec()

    with pytest.raises(ImplementationHandoffError, match="human_confirmation"):
        build_connector_handoff(
            spec, _artifact(spec, _override(source="agent_recommendation"))
        )


def test_blocker_is_auditable_before_approval():
    blocker = detect_blocking_unresolved_fields(_bosch_spec(), (_requirement(),))[0]

    assert blocker.field_path == "signals[0].unit"
    assert blocker.semantic_resolution == "unresolved"
    assert blocker.semantic_value["name"] is None
    assert blocker.best_supported_candidate["symbol"] == "mg"
    assert blocker.supporting_evidence_refs
    assert "acquisition-to-HDF5" in blocker.remaining_uncertainty


def test_valid_handoff_retains_approval_provenance_and_connector_context():
    spec = _bosch_spec()
    handoff = build_connector_handoff(spec, _artifact(spec, _override()))

    saved = handoff.implementation_overrides[0]
    assert saved.source == "human_confirmation"
    assert saved.approved_by == "user"
    assert saved.evidence_refs == tuple(sorted(_EVIDENCE_IDS))
    assert saved.rationale
    assert handoff.downstream_context["dataset_id"] == "boschresearch/cnc-machining"


def test_unknown_evidence_reference_is_rejected():
    spec = _bosch_spec()
    unknown = replace(_requirement(), supporting_evidence_refs=("ev_unknown",))

    with pytest.raises(ImplementationHandoffError, match="absent"):
        build_connector_handoff(
            spec,
            _artifact(spec, _override(), requirements=(unknown,)),
            evidence_ids=_EVIDENCE_IDS,
        )


def test_bosch_timef_unit_uses_unambiguous_pint_name():
    from timenet.types import ureg

    spec = _bosch_spec()
    handoff = build_connector_handoff(spec, _artifact(spec, _override()))
    unit_name = handoff.implementation_view["signals[0].unit"]["value"]["timef_unit"]

    assert str(ureg.Unit(unit_name)) == "millistandard_gravity"
    assert str(ureg.Unit("mg")) == "milligram"


def test_bosch_handoff_fixture_remains_unresolved():
    spec = _bosch_spec()

    assert spec.signals[0].unit.resolution.status is EvidenceStatus.UNRESOLVED
    assert detect_blocking_unresolved_fields(spec, (_requirement(),))


def test_kuka_regression_validation_is_unchanged():
    spec = load_kuka_collision_part1_spec()
    profile = read_dataset_profile(_KUKA_PROFILE_PATH)

    assert validate_dataset_spec(profile, spec).valid
