from robot_observability.qa import HELDOUT_PROMPTS, INTENTS, PROMPTS, channel_descriptions, target_text


def test_channel_descriptions_do_not_leak_window_statistics() -> None:
    metadata = {
        "raw_mean_nm": [123.456] * 7,
        "raw_std_nm": [234.567] * 7,
        "raw_rms_nm": [345.678] * 7,
        "raw_max_abs_nm": [456.789] * 7,
    }
    descriptions = " ".join(channel_descriptions(metadata))
    for leaked_value in ("123.456", "234.567", "345.678", "456.789"):
        assert leaked_value not in descriptions


def test_dynamic_answer_precedes_evidence() -> None:
    metadata = {
        "contact": False,
        "event_type": "free",
        "onset_sample": None,
        "strongest_joint": None,
        "affected_joints": [],
        "evidence_start_ms": None,
        "evidence_end_ms": None,
        "joint_scores": [0.0] * 7,
    }
    assert target_text(metadata, "contact").startswith('Answer: {"contact":false}\nEvidence:')
    rationale_target = target_text(metadata, "contact", "rationale_then_answer")
    assert rationale_target.startswith("Rationale:")
    assert "contact" in rationale_target.split("Answer:", 1)[0]
    assert "free-motion" not in rationale_target
    assert rationale_target.endswith('Answer: {"contact":false}')
    assert target_text(metadata, "contact", "answer_only") == 'Answer: {"contact":false}'


def test_heldout_paraphrases_are_disjoint_from_training_prompts() -> None:
    for intent in INTENTS:
        assert set(PROMPTS[intent]).isdisjoint(HELDOUT_PROMPTS[intent])


def test_rationale_changes_with_conversational_intent() -> None:
    metadata = {
        "record_id": "event-1",
        "contact": True,
        "event_type": "accidental",
        "onset_sample": 400,
        "strongest_joint": "J3",
        "affected_joints": ["J3", "J2"],
        "evidence_start_ms": 400,
        "evidence_end_ms": 550,
        "joint_scores": [0.1, 0.8, 1.4, 0.5, 0.2, 0.1, 0.0],
    }
    rationales = {
        intent: target_text(metadata, intent, "rationale_then_answer").split("Answer:", 1)[0]
        for intent in INTENTS
    }

    assert len(set(rationales.values())) == len(INTENTS)
    assert "first sustained" in rationales["onset"] or "first appears" in rationales["onset"]
    assert "1.40" in rationales["strongest_joint"]
    assert "J3, J2" in rationales["affected_joints"]
