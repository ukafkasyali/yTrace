"""Natural-language prompt families and machine-parseable answer targets."""

from __future__ import annotations

import hashlib
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


def evidence_sentence(metadata: dict[str, object], intent: Intent = "summary") -> str:
    """Create an intent-specific rationale from auditable signal measurements."""
    if not metadata["contact"]:
        free_rationales: dict[Intent, tuple[str, str]] = {
            "summary": (
                "Across J1–J7, no sustained torque disturbance crosses the training-calibrated threshold, so no localized onset or responsible joint is supported.",
                "All seven normalized torque channels remain below the sustained-disturbance criterion, leaving no temporal or joint-specific contact evidence.",
            ),
            "contact": (
                "None of the seven normalized torque channels shows a sustained threshold crossing, so external contact is not supported.",
                "The channel-wise disturbance scores remain below the calibrated contact criterion throughout the window.",
            ),
            "semantics": (
                "The window contains no sustained torque excursion or localized evidence interval, which supports the undisturbed interaction category.",
                "No joint departs persistently from its training-calibrated no-contact range, supporting the final motion category.",
            ),
            "onset": (
                "No sustained disturbance transition is detected, so a contact onset cannot be localized in this window.",
                "Because every channel remains below the disturbance criterion, there is no defensible onset timestamp.",
            ),
            "strongest_joint": (
                "No joint crosses the calibrated disturbance threshold, so assigning a responsible joint would be unsupported.",
                "The normalized channel scores provide no threshold-crossing evidence for a strongest affected joint.",
            ),
            "affected_joints": (
                "No channel has a sustained threshold crossing, so the affected-joint set is empty.",
                "All seven joint scores remain below the affected-channel criterion, leaving no joints to rank.",
            ),
            "evidence_interval": (
                "No sustained excursion is present from which to define a temporal evidence interval.",
                "The signal never enters a persistent disturbance regime, so interval bounds cannot be supported.",
            ),
        }
        options = free_rationales[intent]
        digest = hashlib.sha256(
            f"{metadata.get('record_id', 'unknown')}:{intent}:rationale".encode()
        ).digest()
        return options[digest[0] % len(options)]

    scores = list(metadata["joint_scores"])
    ranked = sorted(zip(JOINT_NAMES, scores), key=lambda pair: -pair[1])
    affected = list(metadata["affected_joints"])
    affected_text = ", ".join(affected)
    onset = metadata["onset_sample"]
    start = metadata["evidence_start_ms"]
    end = metadata["evidence_end_ms"]
    duration = int(end) - int(start)
    leader, leader_score = ranked[0]
    runner_up, runner_up_score = ranked[1]
    rationales: dict[Intent, tuple[str, str]] = {
        "summary": (
            f"A sustained torque excursion begins near {onset} ms and spans {start}–{end} ms; {leader} has the largest normalized disturbance, with threshold-crossing support from {affected_text}.",
            f"The signal enters a persistent disturbance regime at about {onset} ms for {duration} ms, led by {leader} and accompanied by threshold crossings on {affected_text}.",
        ),
        "contact": (
            f"A sustained normalized torque excursion begins near {onset} ms and persists across the {start}–{end} ms evidence interval, supporting external contact.",
            f"The telemetry leaves its calibrated no-contact range at about {onset} ms and remains disturbed for {duration} ms.",
        ),
        "semantics": (
            f"The disturbance starts near {onset} ms, lasts {duration} ms, and affects {len(affected)} joints led by {leader}; these temporal and cross-joint properties support the final interaction category.",
            f"A localized excursion spans {start}–{end} ms with {leader} dominant and {len(affected)} threshold-crossing joints, providing the evidence for the final category.",
        ),
        "onset": (
            f"The first sustained departure from the calibrated baseline occurs near {onset} ms, after which the disturbance continues through {end} ms.",
            f"Persistent multi-sample torque evidence first appears at about {onset} ms; earlier fluctuations do not sustain the disturbance criterion.",
        ),
        "strongest_joint": (
            f"{leader} has the largest normalized disturbance score ({leader_score:.2f}), ahead of {runner_up} ({runner_up_score:.2f}), making it the strongest joint-level evidence.",
            f"The disturbance ranking is led by {leader} at {leader_score:.2f}, exceeding the next-highest channel {runner_up} at {runner_up_score:.2f}.",
        ),
        "affected_joints": (
            f"The channels crossing the calibrated disturbance threshold are {affected_text}, ordered by their normalized evidence strength.",
            f"Sustained threshold-crossing support is present on {affected_text}; the remaining channels do not meet the affected-joint criterion.",
        ),
        "evidence_interval": (
            f"The sustained disturbance regime begins at {start} ms and ends at {end} ms, defining a {duration} ms evidence interval.",
            f"Persistent torque evidence is concentrated from {start} to {end} ms; outside those bounds it does not sustain the interval criterion.",
        ),
    }
    options = rationales[intent]
    digest = hashlib.sha256(f"{metadata.get('record_id', 'unknown')}:{intent}:rationale".encode()).digest()
    return options[digest[0] % len(options)]


def target_text(
    metadata: dict[str, object],
    intent: Intent,
    output_format: str = "answer_then_evidence",
) -> str:
    payload = json.dumps(answer_payload(metadata, intent), separators=(",", ":"), sort_keys=True)
    rationale = evidence_sentence(metadata, intent)
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
