from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np

from ..io.matlab import load_matlab


def _locate_variable(run_dir: Path, variable: str) -> tuple[Path, np.ndarray]:
    for path in sorted(run_dir.glob("*.mat")):
        values = load_matlab(path).variables
        if variable in values:
            return path, np.asarray(values[variable])
    raise ValueError(f"Variable {variable!r} not found in {run_dir}")


def plot_run(
    run_dir: str | Path,
    *,
    variable: str = "MsrExtTrq",
    event_indices: Iterable[int] | None = None,
):
    """Plot every data channel. Event indices, when supplied, are MATLAB one-based."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError("Plotting requires the 'plot' dependency extra") from error

    directory = Path(run_dir)
    source_file, array = _locate_variable(directory, variable)
    if array.ndim != 2 or array.shape[0] < 2:
        raise ValueError(f"Expected a channels-by-samples matrix with a time row, got {array.shape}")
    time = array[0]
    channels = array[1:]
    figure, axis = plt.subplots(figsize=(12, 6))
    for index, values in enumerate(channels, start=1):
        axis.plot(time, values, linewidth=0.8, label=f"channel {index}")
    for index in ([] if event_indices is None else event_indices):
        if 1 <= index <= time.size:
            axis.axvline(time[index - 1], color="black", alpha=0.18, linewidth=0.7)
    axis.set(title=f"{directory.name}: {variable} ({source_file.name})", xlabel="Time (s)", ylabel=variable)
    axis.legend(ncol=2)
    axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure, axis
