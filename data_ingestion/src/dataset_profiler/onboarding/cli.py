"""Thin command-line interface over the public onboarding Python API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .models import SourceDescriptor
from .orchestrator import OnboardingOrchestrator
from .presets import bosch_reference_backend


def _service(args: argparse.Namespace) -> OnboardingOrchestrator:
    if args.workflow != "bosch-cnc-reference-v1":
        raise ValueError(f"unsupported workflow {args.workflow!r}")
    return OnboardingOrchestrator(
        args.jobs_root,
        bosch_reference_backend(args.timenet_repo),
    )


def _json_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"value must be valid JSON: {exc}") from exc


def _print_job(job: Any) -> None:
    print(json.dumps(job.to_dict(), indent=2, allow_nan=False))


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser without executing a workflow."""
    parser = argparse.ArgumentParser(prog="onboard")
    parser.add_argument(
        "--jobs-root",
        type=Path,
        default=Path("outputs/onboarding_jobs"),
    )
    parser.add_argument("--timenet-repo", type=Path)
    parser.add_argument("--workflow", default="bosch-cnc-reference-v1")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("source", type=Path)
    create.add_argument("--dataset-id", default="bosch-cnc")
    create.add_argument("--documentation", type=Path, action="append", default=[])
    for name in ("status", "run", "continue"):
        command = commands.add_parser(name)
        command.add_argument("job_id")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("job_id")
    resolve.add_argument("--field-path", required=True)
    resolve.add_argument("--value", required=True, type=_json_value)
    resolve.add_argument("--approved-by", required=True)
    resolve.add_argument("--rationale", required=True)
    return parser


def main() -> int:
    """Dispatch a CLI command to the same orchestration service used by Python callers."""
    args = build_parser().parse_args()
    service = _service(args)
    if args.command == "create":
        job = service.create_job(
            SourceDescriptor(
                source_type="local_directory",
                local_path=str(args.source.resolve()),
                dataset_id=args.dataset_id,
                documentation_paths=tuple(
                    str(path.resolve()) for path in args.documentation
                ),
            )
        )
    elif args.command == "status":
        job = service.get_job(args.job_id)
    elif args.command == "run":
        job = service.run_job(args.job_id)
    elif args.command == "continue":
        job = service.continue_job(args.job_id)
    else:
        job = service.resolve_blocker(
            args.job_id,
            field_path=args.field_path,
            value=args.value,
            approved_by=args.approved_by,
            rationale=args.rationale,
        )
    _print_job(job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
