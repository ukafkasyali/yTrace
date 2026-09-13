"""Frozen onboarding evidence must stay bound to its profiled source files."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from dataset_profiler.onboarding import SourceDescriptor
from dataset_profiler.onboarding.backend import LocalOnboardingBackend


def _backend(profile_path):
    return LocalOnboardingBackend(
        workflow_id="source-binding-test",
        semantic_client=None,
        requirements=(),
        timenet=object(),
        seed_profile=profile_path,
    )


def _source(root):
    source = SourceDescriptor(
        source_type="local_directory",
        local_path=str(root),
        dataset_id="fixture",
    )
    return SimpleNamespace(source=source)


def _write_profile(path, relative_path, content):
    path.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "relative_path": relative_path,
                        "size_bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_frozen_profile_accepts_the_exact_profiled_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    content = b"recorded telemetry"
    (source / "run.h5").write_bytes(content)
    profile = tmp_path / "profile.json"
    _write_profile(profile, "run.h5", content)

    result = _backend(profile).profile(_source(source))

    assert result["files"][0]["relative_path"] == "run.h5"


def test_frozen_profile_rejects_changed_source_content(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = b"recorded telemetry"
    (source / "run.h5").write_bytes(b"tampered telemetry")
    profile = tmp_path / "profile.json"
    _write_profile(profile, "run.h5", original)

    with pytest.raises(ValueError, match="content changed"):
        _backend(profile).profile(_source(source))


def test_frozen_profile_rejects_inventory_paths_outside_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.h5"
    content = b"recorded telemetry"
    outside.write_bytes(content)
    profile = tmp_path / "profile.json"
    _write_profile(profile, "../outside.h5", content)

    with pytest.raises(ValueError, match="escapes the source directory"):
        _backend(profile).profile(_source(source))
