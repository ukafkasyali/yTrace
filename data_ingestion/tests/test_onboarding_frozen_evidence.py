"""Frozen semantic claims must be backed by the saved bounded evidence trace."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dataset_profiler.onboarding.backend import LocalOnboardingBackend


_SPEC = Path(__file__).with_name("fixtures") / "bosch_unresolved_spec.json"


def _backend(tmp_path: Path, trace: dict | None):
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    trace_path = None
    if trace is not None:
        trace_path = tmp_path / "trace.json"
        trace_path.write_text(json.dumps(trace), encoding="utf-8")
    return LocalOnboardingBackend(
        workflow_id="frozen-evidence-test",
        semantic_client=None,
        requirements=(),
        timenet=object(),
        seed_semantic_spec=_SPEC,
        seed_semantic_trace=trace_path,
    )


def test_frozen_spec_requires_the_saved_agent_trace(tmp_path):
    with pytest.raises(ValueError, match="requires its bounded evidence trace"):
        _backend(tmp_path, None)


def test_frozen_spec_rejects_evidence_self_attested_only_in_spec(tmp_path):
    backend = _backend(tmp_path, {"tool_calls": []})

    with pytest.raises(ValueError, match="evidence absent from its trace"):
        backend.semantic_analysis(SimpleNamespace(), tmp_path / "profile.json")
