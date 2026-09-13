"""Bounded, validator-guided DatasetSpec repair (maximum two rounds)."""
from __future__ import annotations
from dataclasses import asdict
import json
from typing import Any
from ..evidence import EvidenceSession
from ..models import DatasetProfile
from ..semantic_spec import DatasetSpec, ValidationResult, validate_dataset_spec
from .agent import ChatClient, SemanticAgent, SemanticAgentRun

MAX_REPAIR_ROUNDS = 2

REPAIR_PROMPT = """Treat the current DatasetSpec as working state. Validator feedback identifies inconsistency, not the correct answer. Preserve fields not implicated by validator failures unless new bounded evidence directly proves they are wrong. Investigate relevant evidence tools before changing semantics. Repair only justified affected fields; do not regenerate a specification from scratch or guess merely to satisfy validation."""

def summarize_validation(result: ValidationResult) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Any]] = {}
    for issue in result.errors:
        groups.setdefault((issue.code, issue.path.rsplit("[", 1)[0]), []).append(issue)
    return [{"code": code, "target": target, "count": len(items), "examples": [asdict(item) for item in items[:3]]} for (code, target), items in sorted(groups.items())]

def repair_dataset_spec(profile: DatasetProfile, session: EvidenceSession, current_spec: DatasetSpec, validator_result: ValidationResult, client: ChatClient) -> SemanticAgentRun:
    context = REPAIR_PROMPT + "\nCURRENT_SPEC:\n" + current_spec.to_json() + "\nGROUPED_VALIDATOR_FEEDBACK:\n" + json.dumps(summarize_validation(validator_result), indent=2)
    return SemanticAgent(client).run(profile, session, user_context=context)


def candidate_score(validation: ValidationResult) -> tuple[int, int, int]:
    """Order candidates deterministically; lower scores are better."""
    unsupported_codes = {
        "INVALID_DOCUMENTATION_EVIDENCE", "INVALID_EVIDENCE_REFERENCE",
        "MISSING_EVIDENCE_REFERENCE", "UNKNOWN_EVIDENCE_REFERENCE",
    }
    unsupported = sum(issue.code in unsupported_codes for issue in validation.errors)
    return (0 if validation.valid else 1, len(validation.errors), unsupported)


def run_repairs(profile: DatasetProfile, documentation: list[Any], initial: DatasetSpec,
                client: ChatClient, *, evidence_ids: set[str] | None = None) -> tuple[list[dict[str, Any]], DatasetSpec, ValidationResult, dict[str, Any]]:
    known_evidence_ids = set(evidence_ids or ())
    current = initial
    validation = validate_dataset_spec(profile, initial, evidence_ids=known_evidence_ids)
    rounds = []
    best_spec, best_validation = current, validation
    best_round, best_score = 0, candidate_score(validation)
    for number in range(1, MAX_REPAIR_ROUNDS + 1):
        if validation.valid:
            break
        run = repair_dataset_spec(profile, EvidenceSession(profile, documentation_sources=documentation), current, validation, client)
        known_evidence_ids.update(trace_evidence_ids(run.trace))
        new_validation = validate_dataset_spec(
            profile, run.spec, evidence_ids=known_evidence_ids
        )
        score = candidate_score(new_validation)
        accepted = score < best_score
        if accepted:
            best_spec, best_validation = run.spec, new_validation
            best_round, best_score = number, score
        rounds.append({"round": number, "input_issue_count": len(validation.errors),
                       "grouped_issues": summarize_validation(validation), "trace": run.trace,
                       "spec": run.spec, "validation": new_validation,
                       "candidate_score": score, "accepted_as_best": accepted,
                       "selection_reason": ("strictly better deterministic score" if accepted
                                            else "rejected: score did not improve on best candidate"),
                       "best_round_after": best_round, "best_score_after": best_score})
        current, validation = best_spec, best_validation
    selection = {"best_round": best_round, "best_score": best_score,
                 "ordering": ["valid", "total_issue_count", "unsupported_claim_issue_count"],
                 "tie_break": "earlier candidate retained"}
    return rounds, best_spec, best_validation, selection


def trace_evidence_ids(trace: dict[str, Any]) -> set[str]:
    """Collect the exact evidence IDs returned in one semantic-agent trace."""
    return {
        evidence_id
        for call in trace.get("tool_calls", [])
        for evidence_id in call.get("returned_evidence_ids", [])
    }
