import pytest

torch = pytest.importorskip("torch")

from robot_observability.cnn_baseline import (
    MultiTaskCnn1D,
    bin_to_sample,
    sample_to_bin,
)


def test_cnn_heads_have_expected_shapes() -> None:
    model = MultiTaskCnn1D(channels=(8, 16), kernel_sizes=(5, 3), dropout=0.0)
    output = model(torch.zeros(4, 7, 1024))
    assert output["semantics"].shape == (4, 3)
    assert output["onset"].shape == (4, 256)
    assert output["strongest_joint"].shape == (4, 7)
    assert output["affected_joints"].shape == (4, 7)


def test_onset_bin_round_trip_is_within_temporal_resolution() -> None:
    samples = torch.tensor([205, 400, 716])
    bins = sample_to_bin(samples, bins=128, window_samples=1024)
    recovered = bin_to_sample(bins, number_of_bins=128, window_samples=1024)
    assert torch.max(torch.abs(recovered - samples)).item() <= 5
