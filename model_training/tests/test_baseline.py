import numpy as np

from robot_observability.baseline import _fixed_indices, trivial_train_prior_prediction


def test_fixed_indices_match_locked_model_evaluators() -> None:
    expected = sorted(np.random.default_rng(20260912).choice(100, size=12, replace=False).tolist())
    assert _fixed_indices(100, 12, 20260912) == expected


def test_train_prior_prediction_uses_only_training_labels() -> None:
    rows = [
        (
            np.zeros((7, 8)),
            {
                "event_type": "free",
                "contact": False,
                "onset_sample": None,
                "strongest_joint": None,
                "affected_joints": [],
                "evidence_start_ms": None,
                "evidence_end_ms": None,
            },
        ),
        *[
            (
                np.zeros((7, 8)),
                {
                    "event_type": "intentional",
                    "contact": True,
                    "onset_sample": onset,
                    "strongest_joint": "J3",
                    "affected_joints": ["J3"],
                    "evidence_start_ms": onset,
                    "evidence_end_ms": onset + 100,
                },
            )
            for onset in (300, 500)
        ],
    ]
    prediction = trivial_train_prior_prediction(rows)
    assert prediction == {
        "contact": True,
        "event_type": "intentional",
        "onset_ms": 400,
        "strongest_joint": "J3",
        "affected_joints": ["J3"],
        "evidence_start_ms": 400,
        "evidence_end_ms": 500,
    }
