from robot_observability.qa import channel_descriptions, target_text


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
