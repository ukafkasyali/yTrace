from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from .jobs import IngestionJobStore
from .providers import ProviderAcquirer
from .service import HttpApprovedSourceResolver
from .worker import AcquisitionWorker


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire queued approved dataset assets")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")

    data_dir = Path(os.environ.get("INGESTION_DATA_DIR", "var/ingestion"))
    jobs = IngestionJobStore(data_dir / "ingestions.sqlite3")
    resolver = HttpApprovedSourceResolver(
        os.environ.get("INGESTION_SOURCING_API_URL", "http://127.0.0.1:8001")
    )
    acquirer = ProviderAcquirer(
        data_dir / "cache",
        github_token=os.environ.get("GITHUB_TOKEN"),
        hugging_face_token=os.environ.get("HF_TOKEN"),
    )
    worker = AcquisitionWorker(jobs, resolver, acquirer)
    try:
        worker.recover_interrupted()
        while True:
            processed = worker.run_once()
            if args.once:
                return 0
            if processed is None:
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    finally:
        acquirer.close()
        resolver.close()
        jobs.close()


if __name__ == "__main__":
    raise SystemExit(main())
