from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from dataset_profiler.io.matlab import load_matlab
from dataset_profiler.visualization.timeseries import plot_run


ROOT = Path("/home/ugur/data/collision-batch-01")
OUTPUT = Path("outputs/collision-batch-01")


def load_signal(run: Path, name: str) -> np.ndarray:
    return np.asarray(load_matlab(run / f"JK_{name}.mat").variables[name])


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    run = ROOT / "03-15-13-13"
    events = np.asarray(
        load_matlab(run / "JK_moments.mat").variables["JK_moments"]
    ).reshape(-1).astype(int)

    figure, _ = plot_run(run, variable="MsrExtTrq", event_indices=events)
    figure.savefig(OUTPUT / "03-15-13-13_external_torque_full.png", dpi=150)
    plt.close(figure)

    data = load_signal(run, "MsrExtTrq")
    mask = data[0] <= 30
    figure, axes = plt.subplots(7, 1, figsize=(14, 14), sharex=True)
    for index, axis in enumerate(axes):
        axis.plot(data[0, mask], data[index + 1, mask], linewidth=0.7)
        axis.set_ylabel(f"J{index + 1}")
        axis.grid(alpha=0.2)
        for event in events:
            if 1 <= event <= data.shape[1] and data[0, event - 1] <= 30:
                axis.axvline(data[0, event - 1], color="crimson", alpha=0.3, linewidth=0.6)
    axes[0].set_title("03-15-13-13: measured external torque, first 30 seconds")
    axes[-1].set_xlabel("Time (s)")
    figure.tight_layout()
    figure.savefig(OUTPUT / "03-15-13-13_external_torque_first_30s.png", dpi=150)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(14, 6))
    for run in sorted(path for path in ROOT.iterdir() if path.is_dir()):
        data = load_signal(run, "MsrExtTrq")
        step = 100
        axis.plot(
            data[0, ::step],
            np.max(np.abs(data[1:, ::step]), axis=0),
            linewidth=0.8,
            label=run.name,
        )
    axis.set_title("Batch 01: maximum absolute external torque across joints")
    axis.set_xlabel("Time (s)")
    axis.set_ylabel("max |torque| across joints")
    axis.grid(alpha=0.2)
    axis.legend(ncol=3, fontsize=8)
    figure.tight_layout()
    figure.savefig(OUTPUT / "batch01_max_absolute_external_torque.png", dpi=150)
    plt.close(figure)

    for path in sorted(OUTPUT.glob("*.png")):
        print(path)


if __name__ == "__main__":
    main()
