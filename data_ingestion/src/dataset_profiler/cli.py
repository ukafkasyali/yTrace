from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import argparse
import json

from .datasets import KukaCollisionHints
from .inspection import inspect_mat_file
from .io.matlab import load_matlab
from .profiler import profile_dataset
from .visualization import plot_run


def _hints(name: str):
    return KukaCollisionHints() if name == "kuka-collision" else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and profile MATLAB time-series datasets")
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect", help="Inspect one raw MAT file")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--first-values", type=int, default=8)

    profile = commands.add_parser("profile", help="Profile and audit a dataset directory")
    profile.add_argument("source", type=Path)
    profile.add_argument("--dataset-id", required=True)
    profile.add_argument("--hints", choices=["none", "kuka-collision"], default="none")
    profile.add_argument("--output", type=Path, default=Path("outputs/dataset_profile.json"))

    plot = commands.add_parser("plot", help="Interactively plot all channels in one run")
    plot.add_argument("run_dir", type=Path)
    plot.add_argument("--variable", default="MsrExtTrq")
    plot.add_argument("--events", action=argparse.BooleanOptionalAction, default=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inspect":
        result = inspect_mat_file(args.path, args.path.parent, args.path.parent.name, args.first_values)
        print(json.dumps(asdict(result), indent=2, allow_nan=False))
        return 0
    if args.command == "profile":
        result = profile_dataset(args.source, args.dataset_id, hints=_hints(args.hints))
        result.write_json(args.output)
        print(
            f"Wrote {args.output}: {len(result.runs)} runs, {len(result.files)} MAT files, "
            f"{len(result.quality.issues)} audit issues"
        )
        return 0
    if args.command == "plot":
        event_indices = []
        if args.events:
            event_path = args.run_dir / "JK_moments.mat"
            if event_path.exists():
                values = load_matlab(event_path).variables.get("JK_moments")
                if values is not None:
                    event_indices = [int(value) for value in values.reshape(-1)]
        plot_run(args.run_dir, variable=args.variable, event_indices=event_indices)
        import matplotlib.pyplot as plt

        plt.show()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
