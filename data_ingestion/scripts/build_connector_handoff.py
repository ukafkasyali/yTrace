"""Build a connector handoff from a semantic spec and human override artifact."""

from __future__ import annotations

import argparse
from pathlib import Path

from dataset_profiler.semantic_spec import (
    DatasetSpec,
    ImplementationOverrideArtifact,
    build_connector_handoff,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-id", action="append", default=[])
    args = parser.parse_args()

    spec = DatasetSpec.read_json(args.spec)
    artifact = ImplementationOverrideArtifact.read_json(args.overrides)
    handoff = build_connector_handoff(
        spec,
        artifact,
        evidence_ids=set(args.evidence_id) if args.evidence_id else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_json(args.output)
    print(f"Wrote {args.output}: connector_ready={handoff.connector_ready}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
