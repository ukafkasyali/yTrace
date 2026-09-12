"""Command-line entry points."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from robot_observability.baseline import run_baseline
from robot_observability.config import DataConfig
from robot_observability.data.prepare import prepare_dataset, prepare_timef_dataset
from robot_observability.data.zenodo import download_raw_corpus

app = typer.Typer(no_args_is_help=True)


@app.command()
def download(output: Path = Path("data/raw"), workers: int = 4, keep_archives: bool = False) -> None:
    """Download, checksum, and extract both Zenodo raw records."""
    download_raw_corpus(output, workers=workers, keep_archives=keep_archives)


@app.command()
def prepare(config: Path = Path("configs/data.yaml")) -> None:
    """Create recording-grouped normalized training windows."""
    typer.echo(json.dumps(prepare_dataset(DataConfig.from_yaml(config)), indent=2))


@app.command("prepare-timef")
def prepare_timef(
    timef_version: Annotated[
        list[Path],
        typer.Option(
            "--timef-version",
            help="TimeF version directory; pass Part I and Part II once each.",
        ),
    ],
    config: Path = Path("configs/data.yaml"),
) -> None:
    """Materialize leakage-safe model windows from canonical TimeF records."""
    typer.echo(
        json.dumps(
            prepare_timef_dataset(DataConfig.from_yaml(config), timef_version),
            indent=2,
        )
    )


@app.command()
def baseline(
    prepared_root: Path = Path("data/prepared/v1"),
    output_root: Path = Path("artifacts/baseline/v1"),
    limit: int = 512,
    selection_seed: int = 20260912,
    wandb_project: str | None = None,
) -> None:
    """Fit and evaluate the transparent signal baseline."""
    typer.echo(
        json.dumps(
            run_baseline(
                prepared_root,
                output_root,
                limit=limit,
                selection_seed=selection_seed,
                wandb_project=wandb_project,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
