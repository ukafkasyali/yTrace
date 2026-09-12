"""Post-generation comparison only; this module is never imported by agent generation."""
from __future__ import annotations

from typing import Any
from ..semantic_spec import DatasetSpec

def compare_specs(candidate: DatasetSpec, reference: DatasetSpec) -> dict[str, Any]:
    """Small field-level report suitable for inspecting the first KUKA experiment."""
    candidate_data, reference_data = candidate.to_dict(), reference.to_dict()
    fields = ("record_discovery", "source_variables", "signals", "time_axes", "events", "provenance", "record_defaults")
    report = {"identity_match": candidate_data["identity"] == reference_data["identity"], "fields": {}}
    for field in fields:
        report["fields"][field] = {"matches_reference": candidate_data.get(field) == reference_data.get(field), "candidate": candidate_data.get(field), "reference": reference_data.get(field)}
    claims = []
    for signal in candidate.signals:
        claims.extend([{"path": f"signals.{signal.source_variable}.semantics", "status": signal.semantics.status, "evidence": list(signal.semantics.evidence)}, {"path": f"signals.{signal.source_variable}.unit", "status": signal.unit.resolution.status, "evidence": list(signal.unit.resolution.evidence)}])
    claims.extend({"path": f"events.{event.source_variable}.semantics", "status": event.semantics.status, "evidence": list(event.semantics.evidence)} for event in candidate.events)
    report["semantic_claims"] = claims
    report["unresolved_claims"] = [claim["path"] for claim in claims if claim["status"] == "unresolved"]
    report["unsupported_claims"] = [claim["path"] for claim in claims if claim["status"] in {"documented", "inferred"} and not claim["evidence"]]
    return report
