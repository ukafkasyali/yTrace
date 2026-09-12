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
        return "No sustained joint disturbance crosses the threshold calibrated on train-only free motion."
    scores = list(metadata["joint_scores"])
    ranked = sorted(zip(JOINT_NAMES, scores), key=lambda pair: -pair[1])
    top = ", ".join(f"{joint}={score:.2f}" for joint, score in ranked[:3])
    return (
        f"The manual event marker is at {metadata['onset_sample']} ms; calibrated top joint scores are {top}. "
        f"Sustained evidence spans {metadata['evidence_start_ms']}–{metadata['evidence_end_ms']} ms."
    )


def target_text(metadata: dict[str, object], intent: Intent) -> str:
    payload = json.dumps(answer_payload(metadata, intent), separators=(",", ":"), sort_keys=True)
    return f"Evidence: {evidence_sentence(metadata)}\nAnswer: {payload}"


def channel_descriptions(metadata: dict[str, object]) -> list[str]:
    means = list(metadata["raw_mean_nm"])
    stds = list(metadata["raw_std_nm"])
    rms = list(metadata["raw_rms_nm"])
    maxima = list(metadata["raw_max_abs_nm"])
    return [
        (
            f"{joint} external torque at 1000 Hz over 1.024 seconds, normalized with train-only robust statistics. "
            f"Raw units are Nm; window mean={means[index]:.4f}, std={stds[index]:.4f}, "
            f"RMS={rms[index]:.4f}, max_abs={maxima[index]:.4f}."
        )
        for index, joint in enumerate(JOINT_NAMES)
    ]
