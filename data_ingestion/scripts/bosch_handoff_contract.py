"""Pure checks that bind the Bosch semantic handoff to its native connector."""

from __future__ import annotations

from typing import Any


DATASET_ID = "boschresearch/cnc-machining"
REQUIRED_TIMEF_UNIT = "milligravity"


def validate_implementation_handoff(
    handoff: dict[str, Any], connector_dataset_id: str
) -> dict[str, Any]:
    """Return explicit compatibility checks for the fixed native implementation."""
    unit_view = handoff.get("implementation_view", {}).get("signals[0].unit", {})
    unit_value = unit_view.get("value", {}) if isinstance(unit_view, dict) else {}
    handoff_unit = (
        unit_value.get("timef_unit") if isinstance(unit_value, dict) else None
    )
    checks = {
        "connector_ready": handoff.get("connector_ready") is True,
        "handoff_dataset_id": handoff.get("dataset_id") == DATASET_ID,
        "connector_dataset_id": connector_dataset_id == DATASET_ID,
        "timef_unit": handoff_unit == REQUIRED_TIMEF_UNIT,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "required_timef_unit": REQUIRED_TIMEF_UNIT,
        "handoff_timef_unit": handoff_unit,
    }
