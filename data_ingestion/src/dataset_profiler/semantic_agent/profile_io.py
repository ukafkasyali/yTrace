"""Decode checked-in JSON profiles for the semantic-agent command."""
from __future__ import annotations

import json
from pathlib import Path
from ..models import AuditIssue, AuditSummary, ChannelStats, DatasetProfile, Event, FileProfile, Inference, RunProfile, VariableProfile

def read_dataset_profile(path: str | Path) -> DatasetProfile:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    files = []
    for item in raw["files"]:
        variables = [VariableProfile(**{**var, "channel_stats": [ChannelStats(**stat) for stat in var.get("channel_stats", [])]}) for var in item["variables"]]
        files.append(FileProfile(**{**item, "variables": variables}))
    runs = []
    for item in raw["runs"]:
        events = [Event(event_id=event["event_id"], observed_index=event["observed_index"], inferred_time_seconds=event.get("inferred_time_seconds"), interpretation=Inference(**event["interpretation"])) for event in item.get("events", [])]
        runs.append(RunProfile(**{**item, "events": events}))
    quality = raw["quality"]
    return DatasetProfile(schema_version=raw["schema_version"], dataset_id=raw["dataset_id"], source=raw["source"], discovery=raw["discovery"], files=files, runs=runs, observed_structure=raw["observed_structure"], signals=raw["signals"], entities=raw["entities"], metadata=raw["metadata"], quality=AuditSummary(checks_run=quality["checks_run"], issues=[AuditIssue(**issue) for issue in quality["issues"]]), inferred_semantics=raw["inferred_semantics"], unknowns=raw["unknowns"])
