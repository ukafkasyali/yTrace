import copy
import math

import pytest

from robot_observability.comparison import percentile, schema_valid, score, usable_summary_text, validate_alignment


def target(contact=True):
    return {
        "contact": contact,
        "event_type": "accidental" if contact else "free",
        "onset_ms": 300 if contact else None,
        "strongest_joint": "J1" if contact else None,
        "affected_joints": ["J1"] if contact else [],
        "evidence_start_ms": 300 if contact else None,
        "evidence_end_ms": 450 if contact else None,
    }


def test_abstention_on_free_motion_is_not_a_correct_negative():
    items = [{"target": target(), "prediction": target()}, {"target": target(False), "prediction": None}]
    result = score(items)
    assert result["contact_accuracy"] == 0.5
    assert result["contact_answer_coverage"] == 0.5
    assert result["contact_positive_f1"] == 1
    assert result["contact_macro_f1"] == 0.5
    assert result["contact_confusion"]["false"] == {"abstain": 1}


@pytest.mark.parametrize("bad", [True, "300", math.nan, math.inf, -1, 1024])
def test_onset_invalid_values_fail_coverage_and_success(bad):
    prediction = target()
    prediction["onset_ms"] = bad
    result = score([{"target": target(), "prediction": prediction}])
    assert result["onset_coverage"] == 0
    assert result["onset_mae_ms"] is None
    assert result["onset_within_50ms_all_contacts"] == 0
    assert not schema_valid(prediction)


def test_missing_joint_lists_do_not_get_free_motion_credit():
    result = score([{"target": target(False), "prediction": None}, {"target": target(), "prediction": {}}])
    assert result["affected_joint_set_f1_contact_only"] == 0


def test_percentile_uses_linear_interpolation():
    assert percentile([1, 2, 3, 10], 0.9) == pytest.approx(7.9)
    assert percentile([], 0.9) is None


def test_keys_alone_are_not_schema_validity():
    p = target()
    p["contact"] = None
    assert not schema_valid(p)


def test_usable_summary_counts_exclude_partial_event_type_answers():
    partial = {"event_type": "free"}
    result = score([
        {"target": target(False), "prediction": target(False)},
        {"target": target(False), "prediction": partial},
    ])
    assert result["semantics_confusion"]["free"] == {"free": 2}
    assert result["usable_semantics_confusion"]["free"] == {"free": 1, "abstain": 1}
    text = usable_summary_text(result)
    assert "1 of 2 windows" in text
    assert "100.00% (1/1)" in text


def test_usable_summary_text_handles_a_contact_only_subset():
    result = score([{"target": target(), "prediction": target()}])
    text = usable_summary_text(result)
    assert "no free-motion windows" in text
    assert "100.00% (1/1)" in text
    assert schema_valid(target())
    assert schema_valid(target(False))
    p = target(False)
    p["event_type"] = "accidental"
    assert not schema_valid(p)
    p = target()
    p["affected_joints"] = [["J1"]]
    assert not schema_valid(p)


def fixture():
    record = dict(target(), record_id="run/event", session_id="run", split="test", onset_sample=300)
    row = {"record_id": "run/event", "target": target(), "prediction": target()}
    return {"a": [row], "b": [copy.deepcopy(row)]}, [record], {"run": "test"}


def test_alignment_accepts_identical_held_out_targets():
    predictions, records, splits = fixture()
    assert validate_alignment(predictions, records, splits) == ["run/event"]


@pytest.mark.parametrize("problem", ["duplicate", "missing", "wrong_target", "train", "wrong_session"])
def test_alignment_rejects_invalid_comparisons(problem):
    predictions, records, splits = fixture()
    if problem == "duplicate":
        predictions["a"].append(copy.deepcopy(predictions["a"][0]))
    elif problem == "missing":
        predictions["b"] = []
    elif problem == "wrong_target":
        predictions["b"][0]["target"]["onset_ms"] = 301
    elif problem == "train":
        splits["run"] = "train"
    else:
        predictions["b"][0]["session_id"] = "different"
    with pytest.raises(ValueError):
        validate_alignment(predictions, records, splits)
