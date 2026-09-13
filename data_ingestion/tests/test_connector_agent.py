"""Connector-writing agent isolation, handoff, and diff-boundary tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from dataset_profiler.onboarding import (
    ConnectorAgentConfig,
    ConnectorAgentError,
    ConnectorCodingAgent,
    JobStage,
    OnboardingJob,
    SourceDescriptor,
)
from dataset_profiler.onboarding.store import JobStore
from dataset_profiler.semantic_spec import (
    DatasetSpec,
    ImplementationOverrideArtifact,
    build_connector_handoff,
    semantic_spec_sha256,
)


_SPEC_PATH = Path(__file__).with_name("fixtures") / "bosch_unresolved_spec.json"
_DATASET_ID = "example/native-sensor"
_CONNECTOR_ROOT = (
    "packages/timenet-connectors/src/timenet_connectors/datasets/example/native_sensor"
)


def _run(*argv: str, cwd: Path) -> str:
    return subprocess.run(
        argv, cwd=cwd, text=True, capture_output=True, check=True
    ).stdout


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "TimeNet"
    repository.mkdir()
    _run("git", "init", "-q", cwd=repository)
    _run("git", "config", "user.email", "test@example.test", cwd=repository)
    _run("git", "config", "user.name", "Test", cwd=repository)
    skill = repository / ".agents/skills/add-dataset-connector"
    skill.mkdir(parents=True)
    (repository / "AGENTS.md").write_text("Use repository skills.\n", encoding="utf-8")
    (skill / "SKILL.md").write_text("Implement a native connector.\n", encoding="utf-8")
    _run("git", "add", ".", cwd=repository)
    _run("git", "commit", "-qm", "pinned base", cwd=repository)
    return repository, _run("git", "rev-parse", "HEAD", cwd=repository).strip()


def _job(tmp_path: Path) -> tuple[OnboardingJob, Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    raw = source / "record.bin"
    raw.write_bytes(b"real-source-bytes")
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    store = JobStore(tmp_path / "jobs")
    job = OnboardingJob(
        job_id="job-agent-test",
        source=SourceDescriptor(
            source_type="local_directory",
            dataset_id="bosch-cnc",
            local_path=str(source),
            discovery_metadata={
                "resources": [
                    {
                        "resource_id": "resource-1",
                        "logical_path": "record.bin",
                        "format": "HDF5",
                        "size_bytes": raw.stat().st_size,
                        "content_sha256": digest,
                    }
                ]
            },
        ),
        workflow_id="test",
        stage=JobStage.CONNECTOR_READY,
    )
    store.create(job)
    spec = DatasetSpec.read_json(_SPEC_PATH)
    overrides = ImplementationOverrideArtifact(
        schema_version="1.0",
        dataset_id=spec.identity.dataset_id,
        semantic_spec_sha256=semantic_spec_sha256(spec),
        requirements=(),
        overrides=(),
        downstream_context={"dataset_id": _DATASET_ID},
    )
    handoff = build_connector_handoff(spec, overrides)
    store.write_artifact(
        job,
        "dataset_profile",
        "semantic/dataset_profile.json",
        {
            "source": {"path": str(source)},
            "files": [
                {
                    "relative_path": "record.bin",
                    "format": "hdf5",
                    "size_bytes": raw.stat().st_size,
                    "sha256": digest,
                    "variables": [
                        {"name": "signal", "shape": [2, 3], "dtype": "float64"}
                    ],
                }
            ],
        },
        JobStage.PROFILING,
    )
    store.write_artifact(
        job,
        "dataset_spec",
        "semantic/dataset_spec.json",
        spec.to_dict(),
        JobStage.VALIDATING,
    )
    store.write_artifact(
        job,
        "implementation_overrides",
        "semantic/implementation_overrides.json",
        overrides.to_dict(),
        JobStage.CONNECTOR_READY,
    )
    handoff_path = store.write_artifact(
        job,
        "connector_handoff",
        "semantic/connector_handoff.json",
        handoff.to_dict(),
        JobStage.CONNECTOR_READY,
    )
    return job, store.job_dir(job.job_id), handoff_path


def _agent_script(tmp_path: Path, *, out_of_scope: bool = False) -> Path:
    script = tmp_path / "fake_agent.py"
    extra = "Path('README.md').write_text('bad\\n')" if out_of_scope else ""
    script.write_text(
        "from pathlib import Path\n"
        f"root = Path({_CONNECTOR_ROOT!r})\n"
        "(root / 'tests').mkdir(parents=True)\n"
        "(root / '__init__.py').write_text('from .connector import CONNECTOR\\n')\n"
        "(root / 'connector.py').write_text('CONNECTOR = object()\\n')\n"
        f"(root / 'dataset.yaml').write_text('dataset_id: {_DATASET_ID}\\n')\n"
        "(root / 'tests/test_connector.py').write_text('def test_connector(): assert True\\n')\n"
        f"{extra}\n",
        encoding="utf-8",
    )
    return script


def test_agent_uses_detached_pinned_worktree_and_persists_bounded_diff(tmp_path: Path):
    repository, revision = _repository(tmp_path)
    job, job_dir, handoff = _job(tmp_path)
    runner = ConnectorCodingAgent(
        ConnectorAgentConfig(
            timenet_repository=repository,
            pinned_revision=revision,
            dataset_id=_DATASET_ID,
            agent_command=(sys.executable, str(_agent_script(tmp_path))),
        )
    )

    result = runner.implement(job, handoff, job_dir)

    assert result["passed"] is True
    assert result["connector_absent_at_pinned_revision"] is True
    assert result["detached_head"] is True
    assert result["worktree_head"] == revision
    assert {item["path"] for item in result["generated_paths"]} == {
        f"{_CONNECTOR_ROOT}/__init__.py",
        f"{_CONNECTOR_ROOT}/connector.py",
        f"{_CONNECTOR_ROOT}/dataset.yaml",
        f"{_CONNECTOR_ROOT}/tests/test_connector.py",
    }
    assert "new file mode" in result["diff"]
    agent_handoff = json.loads(Path(result["agent_handoff_path"]).read_text())
    assert agent_handoff["integration_boundary"] == "CONNECTOR_READY"
    assert agent_handoff["verified_dataset_paths"]["source_root"].endswith("/source")
    assert agent_handoff["resource_inventory"][0]["sha256"]
    assert agent_handoff["implementation_override_artifact"]["schema_version"] == "1.0"


def test_agent_changes_outside_connector_folder_are_rejected(tmp_path: Path):
    repository, revision = _repository(tmp_path)
    job, job_dir, handoff = _job(tmp_path)
    runner = ConnectorCodingAgent(
        ConnectorAgentConfig(
            timenet_repository=repository,
            pinned_revision=revision,
            dataset_id=_DATASET_ID,
            agent_command=(
                sys.executable,
                str(_agent_script(tmp_path, out_of_scope=True)),
            ),
        )
    )

    with pytest.raises(
        ConnectorAgentError, match="outside the connector-only boundary"
    ):
        runner.implement(job, handoff, job_dir)
