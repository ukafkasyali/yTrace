"""Natural-language prompt families and machine-parseable answer targets."""

from __future__ import annotations

import json
from typing import Literal

from robot_observability.constants import JOINT_NAMES

Intent = Literal[
    "summary",
    "contact",
    "semantics",
    "onset",
    "strongest_joint",
    "affected_joints",
    "evidence_interval",
]

INTENTS: tuple[Intent, ...] = (
    "summary",
    "contact",
    "semantics",
    "onset",
    "strongest_joint",
    "affected_joints",
    "evidence_interval",
)

PROMPTS: dict[Intent, tuple[str, ...]] = {
    "summary": (
        "Diagnose this robot telemetry window.",
        "Summarize the contact event and identify the supporting temporal evidence.",
    ),
    "contact": (
        "Did external contact occur?",
        "Does this window contain evidence of physical contact?",
    ),
    "semantics": (
        "Was the motion free, an intentional contact, or an accidental collision?",
        "Classify the interaction semantics.",
    ),
    "onset": (
        "When did external contact begin?",
        "Give the contact onset relative to the start of this window in milliseconds.",
    ),
    "strongest_joint": (
        "Which joint has the strongest normalized disturbance evidence?",
        "Identify the joint whose torque signal most strongly supports the diagnosis.",
    ),
    "affected_joints": (
        "Which joints are materially affected, ranked by disturbance?",
        "List the joints crossing the calibrated disturbance threshold.",
    ),
    "evidence_interval": (
        "Where is the strongest temporal evidence?",
        "Return the interval containing sustained disturbance evidence.",
    ),
}


def answer_payload(metadata: dict[str, object], intent: Intent) -> dict[str, object]:
    complete = {
        "contact": bool(metadata["contact"]),
        "event_type": metadata["event_type"],
        "onset_ms": metadata["onset_sample"],
        "strongest_joint": metadata["strongest_joint"],
        "affected_joints": metadata["affected_joints"],
        "evidence_start_ms": metadata["evidence_start_ms"],
        "evidence_end_ms": metadata["evidence_end_ms"],
    }
    keys: dict[Intent, tuple[str, ...]] = {
        "summary": tuple(complete),
        "contact": ("contact",),
        "semantics": ("event_type",),
        "onset": ("onset_ms",),
        "strongest_joint": ("strongest_joint",),
        "affected_joints": ("affected_joints",),
        "evidence_interval": ("evidence_start_ms", "evidence_end_ms"),
    }
    return {key: complete[key] for key in keys[intent]}


def evidence_sentence(metadata: dict[str, object]) -> str:
    if not metadata["contact"]:
        return (
            "Across J1–J7, no sustained torque disturbance crosses the threshold calibrated only "
            "on training no-contact recordings, so no localized onset or responsible joint is supported."
        )
    scores = list(metadata["joint_scores"])
    ranked = sorted(zip(JOINT_NAMES, scores), key=lambda pair: -pair[1])
    affected = list(metadata["affected_joints"])
    affected_text = ", ".join(affected) if affected else "no additional joints"
    return (
        f"A sustained torque excursion begins near {metadata['onset_sample']} ms and its strongest "
        f"evidence spans {metadata['evidence_start_ms']}–{metadata['evidence_end_ms']} ms; "
        f"{ranked[0][0]} has the largest normalized disturbance, with threshold-crossing support "
        f"from {affected_text}."
    )


def target_text(
    metadata: dict[str, object],
    intent: Intent,
    output_format: str = "answer_then_evidence",
) -> str:
    payload = json.dumps(answer_payload(metadata, intent), separators=(",", ":"), sort_keys=True)
    rationale = evidence_sentence(metadata)
    if output_format == "answer_then_evidence":
        return f"Answer: {payload}\nEvidence: {rationale}"
    if output_format == "rationale_then_answer":
        return f"Rationale: {rationale}\nAnswer: {payload}"
    raise ValueError(f"Unknown output format: {output_format}")


def channel_descriptions(metadata: dict[str, object]) -> list[str]:
    del metadata
    return [
        (
            f"{joint} external joint torque in Nm, sampled at 1000 Hz over 1.024 seconds. "
            "The numeric values are normalized with train-only robust statistics."
        )
        for joint in JOINT_NAMES
    ]
