"""Bounded, validator-guided DatasetSpec repair (maximum two rounds)."""
from __future__ import annotations
from collections import Counter
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

def run_repairs(profile: DatasetProfile, documentation: list[Any], initial: DatasetSpec, client: ChatClient) -> tuple[list[dict[str, Any]], DatasetSpec, ValidationResult]:
    current, validation, rounds = initial, validate_dataset_spec(profile, initial), []
    for number in range(1, MAX_REPAIR_ROUNDS + 1):
        if validation.valid: break
        run = repair_dataset_spec(profile, EvidenceSession(profile, documentation_sources=documentation), current, validation, client)
        new_validation = validate_dataset_spec(profile, run.spec)
        rounds.append({"round": number, "input_issue_count": len(validation.errors), "grouped_issues": summarize_validation(validation), "trace": run.trace, "spec": run.spec, "validation": new_validation})
        current, validation = run.spec, new_validation
    return rounds, current, validation
