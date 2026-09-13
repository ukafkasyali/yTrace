"""The Bosch native connector must reject incompatible human overrides."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "bosch_handoff_contract.py"
)
_SPEC = importlib.util.spec_from_file_location("bosch_handoff_contract", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _handoff(unit: str):
    return {
        "connector_ready": True,
        "dataset_id": "boschresearch/cnc-machining",
        "implementation_view": {
            "signals[0].unit": {"value": {"timef_unit": unit}}
        },
    }


def test_accepts_the_unit_used_by_the_native_connector():
    result = _MODULE.validate_implementation_handoff(
        _handoff("milligravity"), "boschresearch/cnc-machining"
    )

    assert result["passed"] is True
    assert all(result["checks"].values())


def test_rejects_a_human_override_the_native_connector_cannot_honor():
    result = _MODULE.validate_implementation_handoff(
        _handoff("meter / second ** 2"), "boschresearch/cnc-machining"
    )

    assert result["passed"] is False
    assert result["checks"]["timef_unit"] is False
    assert result["handoff_timef_unit"] == "meter / second ** 2"
