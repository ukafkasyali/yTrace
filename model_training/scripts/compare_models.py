"""Combine like-for-like held-out metrics into one machine-readable report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

METRICS = (
    "n",
    "parse_validity",
    "contact_accuracy",
    "contact_f1",
    "semantics_accuracy",
    "semantics_macro_f1",
    "strongest_joint_accuracy",
    "onset_mae_ms",
    "onset_median_ae_ms",
    "onset_p90_ae_ms",
    "onset_within_50ms",
)


def load_metrics(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    metrics = payload.get("test", payload)
    if not isinstance(metrics, dict):
        raise TypeError(f"Expected a metrics object in {path}")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--result",
        action="append",
        required=True,
        metavar="LABEL=METRICS_JSON",
        help="Repeat for CNN, direct zero/one-shot, and OpenTSLM.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    models: dict[str, dict[str, object]] = {}
    for specification in args.result:
        if "=" not in specification:
            raise ValueError(f"Result must be LABEL=PATH, received: {specification}")
        label, raw_path = specification.split("=", 1)
        metrics = load_metrics(Path(raw_path))
        models[label] = {key: metrics.get(key) for key in METRICS}
    sample_counts = {metrics.get("n") for metrics in models.values()}
    report = {
        "split": "test",
        "models": models,
        "comparable_sample_counts": len(sample_counts) == 1,
        "warnings": []
        if len(sample_counts) == 1
        else ["Sample counts differ; rerun every model with the same fixed held-out subset."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
