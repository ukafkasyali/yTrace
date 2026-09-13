"""Fail-closed checks for persisted onboarding artifact identity."""

from __future__ import annotations

import hashlib
import json

import pytest

from dataset_profiler.onboarding.models import JobStage, OnboardingJob, SourceDescriptor
from dataset_profiler.onboarding.store import JobStore


def _job() -> OnboardingJob:
    return OnboardingJob(
        job_id="job-integrity-test",
        source=SourceDescriptor(
            source_type="local_directory",
            local_path="/tmp/source",
            dataset_id="fixture",
        ),
        workflow_id="fixture-workflow",
    )


def _stored(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = _job()
    store.create(job)
    artifact = store.write_artifact(
        job,
        "profile",
        "semantic/profile.json",
        {"dataset_id": "fixture"},
        JobStage.PROFILING,
    )
    return store, job, artifact


def test_clean_artifact_is_verified_on_resume_and_read(tmp_path):
    store, job, artifact = _stored(tmp_path)

    resumed = store.load(job.job_id)

    assert resumed.artifact_path(store.job_dir(job.job_id), "profile") == artifact.resolve()


def test_modified_artifact_is_rejected_on_resume_and_later_read(tmp_path):
    store, job, artifact = _stored(tmp_path)
    loaded = store.load(job.job_id)
    artifact.write_text('{"dataset_id":"changed"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="stored SHA-256"):
        store.load(job.job_id)
    with pytest.raises(ValueError, match="stored SHA-256"):
        loaded.artifact_path(store.job_dir(job.job_id), "profile")


def test_persisted_path_cannot_escape_job_directory(tmp_path):
    store, job, _ = _stored(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"trusted":false}\n', encoding="utf-8")
    state_path = store.job_dir(job.job_id) / "job.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifacts"]["profile"]["path"] = "../../outside.json"
    state["artifacts"]["profile"]["sha256"] = hashlib.sha256(
        outside.read_bytes()
    ).hexdigest()
    JobStore.write_json(state_path, state)

    with pytest.raises(ValueError, match="outside the job directory"):
        store.load(job.job_id)


def test_artifact_write_rejects_traversal(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = _job()
    store.create(job)

    with pytest.raises(ValueError, match="outside the job directory"):
        store.write_artifact(
            job,
            "escape",
            "../../escape.json",
            {"trusted": False},
            JobStage.PROFILING,
        )

    assert not (tmp_path / "escape.json").exists()


def test_persisted_job_identity_must_match_directory(tmp_path):
    store, job, _ = _stored(tmp_path)
    state_path = store.job_dir(job.job_id) / "job.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["job_id"] = "job-different"
    JobStore.write_json(state_path, state)

    with pytest.raises(ValueError, match="job_id does not match"):
        store.load(job.job_id)
