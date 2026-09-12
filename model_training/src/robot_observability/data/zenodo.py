"""Resumable Zenodo download utilities shared by the CLI and TimeNet connector."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from robot_observability.constants import ZENODO_RECORDS


def emit(log_path: Path, event: str, **fields: object) -> None:
    payload = {"timestamp": time.time(), "event": event, **fields}
    line = json.dumps(payload, sort_keys=True)
    print(line, flush=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def digest(path: Path, algorithm: str) -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def fetch_record(record_id: int) -> dict[str, object]:
    with urllib.request.urlopen(f"https://zenodo.org/api/records/{record_id}", timeout=60) as response:
        return json.load(response)


def download_one(file_meta: dict[str, object], destination: Path, log_path: Path) -> Path:
    name = str(file_meta["key"])
    target = destination / name
    checksum_type, checksum_value = str(file_meta["checksum"]).split(":", 1)
    if target.exists() and digest(target, checksum_type) == checksum_value:
        emit(log_path, "download_skip_verified", file=name, bytes=target.stat().st_size)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    links = file_meta["links"]
    url = str(links.get("content") or links["self"])
    emit(log_path, "download_start", file=name, bytes=file_meta.get("size"), url=url)
    subprocess.run(
        [
            "curl",
            "--location",
            "--fail",
            "--continue-at",
            "-",
            "--retry",
            "8",
            "--retry-all-errors",
            "--output",
            str(target),
            url,
        ],
        check=True,
    )
    actual = digest(target, checksum_type)
    if actual != checksum_value:
        raise RuntimeError(f"Checksum mismatch for {target}: expected {checksum_value}, got {actual}")
    emit(log_path, "download_complete", file=name, bytes=target.stat().st_size)
    return target


def extract_one(archive: Path, output_root: Path, log_path: Path) -> None:
    marker = output_root / ".extracted" / f"{archive.name}.ok"
    if marker.exists():
        emit(log_path, "extract_skip", file=archive.name)
        return
    output_root.mkdir(parents=True, exist_ok=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    emit(log_path, "extract_start", file=archive.name)
    subprocess.run(["tar", "--zstd", "-xf", str(archive), "-C", str(output_root)], check=True)
    marker.touch()
    emit(log_path, "extract_complete", file=archive.name)


def download_raw_corpus(output: Path, *, workers: int = 4, keep_archives: bool = False) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / "download_state.jsonl"
    source_labels = {"collision": "accidental", "contact": "intentional"}
    for source_label, semantic_label in source_labels.items():
        record_id = ZENODO_RECORDS[semantic_label]
        record = fetch_record(record_id)
        files = [item for item in record["files"] if str(item["key"]).endswith(".tar.zst")]
        archive_root = output / "archives" / source_label
        archive_root.mkdir(parents=True, exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(download_one, item, archive_root, log_path) for item in files]
            archives = [future.result() for future in concurrent.futures.as_completed(futures)]
        for archive in sorted(archives):
            extract_one(archive, output / source_label, log_path)
            if not keep_archives:
                archive.unlink()
                emit(log_path, "archive_removed", file=archive.name)
    emit(log_path, "all_complete")
    return output
