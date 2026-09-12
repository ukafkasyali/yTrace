from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

from robot_observability.plot_baseline import Demonstration, build_messages


def test_one_shot_messages_precede_unlabeled_test_query(tmp_path: Path) -> None:
    demonstration_image = tmp_path / "demonstration.png"
    test_image = tmp_path / "test.png"
    demonstration_image.write_bytes(b"demo")
    test_image.write_bytes(b"test")
    demonstration = Demonstration(
        image_path=demonstration_image,
        prompt="training question",
        answer='Answer: {"contact":false}',
        record_id="train-1",
    )
    messages = build_messages(test_image, "test question", [demonstration])
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == demonstration.answer
    assert "test question" in messages[-1]["content"][-1]["text"]


def test_zero_shot_has_only_the_test_query(tmp_path: Path) -> None:
    image = tmp_path / "test.png"
    image.write_bytes(b"test")
    messages = build_messages(image, "test question")
    assert [message["role"] for message in messages] == ["user"]
