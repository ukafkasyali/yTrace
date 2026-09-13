"""Bounded coding-agent bridge for native TimeNet connector implementation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from ..semantic_spec import (
    DatasetSpec,
    ImplementationOverrideArtifact,
    semantic_spec_sha256,
)
from .models import OnboardingJob


_DATASET_ID = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_MAX_INVENTORY_ENTRIES = 10_000
_MAX_HANDOFF_BYTES = 4 * 1024 * 1024
_MAX_AGENT_OUTPUT_CHARS = 100_000
_MAX_DIFF_CHARS = 4 * 1024 * 1024


@dataclass(frozen=True)
class ConnectorAgentConfig:
    """Immutable configuration for one native connector-writing capability."""

    timenet_repository: Path
    pinned_revision: str
    dataset_id: str
    agent_command: tuple[str, ...] = (
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--color",
        "never",
    )
    focused_test_command: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not _DATASET_ID.fullmatch(self.dataset_id):
            raise ValueError("TimeNet connector dataset_id must have org/name form")
        if not re.fullmatch(r"[0-9a-f]{40}", self.pinned_revision):
            raise ValueError(
                "pinned TimeNet revision must be a full lowercase commit SHA"
            )
        if not self.agent_command:
            raise ValueError("coding-agent command must not be empty")


class ConnectorCodingAgent:
    """Invoke the existing TimeNet skill in an isolated pinned worktree."""

    def __init__(self, config: ConnectorAgentConfig) -> None:
        self.config = config

    def implement(
        self, job: OnboardingJob, handoff_path: Path, job_dir: Path
    ) -> dict[str, Any]:
        """Write one connector and return an auditable, bounded implementation receipt."""
        repository = self.config.timenet_repository.expanduser().resolve()
        self._git(
            repository, "cat-file", "-e", f"{self.config.pinned_revision}^{{commit}}"
        )
        connector_path = self._connector_path()
        absent = (
            self._git(
                repository,
                "cat-file",
                "-e",
                f"{self.config.pinned_revision}:{connector_path}/connector.py",
                check=False,
            ).returncode
            != 0
        )
        if not absent:
            raise ValueError(
                f"connector {self.config.dataset_id!r} already exists at the pinned revision"
            )

        worktree = (job_dir / "connector" / "timenet-worktree").resolve()
        self._ensure_worktree(repository, worktree)
        agent_handoff = job_dir / "connector" / "agent_handoff.json"
        payload = self._build_handoff(job, handoff_path, job_dir, worktree)
        encoded = json.dumps(payload, indent=2, allow_nan=False).encode() + b"\n"
        if len(encoded) > _MAX_HANDOFF_BYTES:
            raise ValueError("connector-agent handoff exceeds its 4 MiB bound")
        agent_handoff.write_bytes(encoded)

        prompt = self._prompt(agent_handoff, worktree)
        completed = subprocess.run(
            (*self.config.agent_command, prompt),
            cwd=worktree,
            text=True,
            capture_output=True,
            check=False,
        )
        paths = self._changed_paths(worktree)
        if completed.returncode:
            raise ConnectorAgentError(
                "coding agent failed while implementing the TimeNet connector",
                self._agent_receipt(completed, paths),
            )
        self._validate_changed_paths(worktree, paths, connector_path)
        required = {
            f"{connector_path}/__init__.py",
            f"{connector_path}/connector.py",
            f"{connector_path}/dataset.yaml",
        }
        if not required.issubset({item["path"] for item in paths}):
            raise ConnectorAgentError(
                "coding agent did not generate the required native connector files",
                self._agent_receipt(completed, paths),
            )
        if not any(
            item["path"].startswith(f"{connector_path}/tests/")
            and item["path"].endswith(".py")
            for item in paths
        ):
            raise ConnectorAgentError(
                "coding agent did not generate relevant connector tests",
                self._agent_receipt(completed, paths),
            )
        self._git(worktree, "add", "-N", "--", *(item["path"] for item in paths))
        diff = self._git(
            worktree, "diff", "--binary", "--no-ext-diff", "HEAD", "--"
        ).stdout
        if len(diff) > _MAX_DIFF_CHARS:
            raise ValueError(
                "generated connector diff exceeds its 4 MiB persistence bound"
            )
        head = self._git(worktree, "rev-parse", "HEAD").stdout.strip()
        return {
            "passed": True,
            "mode": "coding_agent_time_net_skill",
            "dataset_id": self.config.dataset_id,
            "timenet_repository": str(repository),
            "timenet_worktree": str(worktree),
            "pinned_revision": self.config.pinned_revision,
            "worktree_head": head,
            "detached_head": self._git(
                worktree, "symbolic-ref", "-q", "HEAD", check=False
            ).returncode
            != 0,
            "connector_absent_at_pinned_revision": absent,
            "absence_probe": f"{self.config.pinned_revision}:{connector_path}/connector.py",
            "agent_handoff_path": str(agent_handoff),
            "agent_handoff_sha256": hashlib.sha256(encoded).hexdigest(),
            "generated_paths": paths,
            "diff": diff,
            "agent": self._agent_receipt(completed, paths),
        }

    def _build_handoff(
        self,
        job: OnboardingJob,
        handoff_path: Path,
        job_dir: Path,
        worktree: Path,
    ) -> dict[str, Any]:
        connector_handoff = self._read_object(handoff_path)
        if connector_handoff.get("connector_ready") is not True:
            raise ValueError("connector-agent invocation requires connector_ready=true")
        context = connector_handoff.get("downstream_context")
        if (
            not isinstance(context, dict)
            or context.get("dataset_id") != self.config.dataset_id
        ):
            raise ValueError(
                "connector handoff does not name the configured TimeNet dataset_id"
            )
        spec_path = job.artifact_path(job_dir, "dataset_spec")
        spec = DatasetSpec.read_json(spec_path)
        overrides_path = job.artifact_path(job_dir, "implementation_overrides")
        overrides = ImplementationOverrideArtifact.read_json(overrides_path)
        if overrides.semantic_spec_sha256 != semantic_spec_sha256(spec):
            raise ValueError(
                "implementation override is not bound to the validated DatasetSpec"
            )
        profile = self._read_object(job.artifact_path(job_dir, "dataset_profile"))
        resources = self._resource_inventory(profile, job.source.discovery_metadata)
        source_root = self._verified_source_root(job, profile)
        focused_test = self._focused_test_command(worktree)
        return {
            "schema_version": "connector-agent-handoff-v1",
            "job_id": job.job_id,
            "integration_boundary": "CONNECTOR_READY",
            "timenet": {
                "repository": str(worktree),
                "pinned_revision": self.config.pinned_revision,
                "dataset_id": self.config.dataset_id,
                "skill": ".agents/skills/add-dataset-connector/SKILL.md",
            },
            "verified_dataset_paths": {
                "source_root": str(source_root),
                "documentation": list(job.source.documentation_paths),
            },
            "source_descriptor": job.source.to_dict(),
            "validated_semantic_spec": spec.to_dict(),
            "implementation_override_artifact": overrides.to_dict(),
            "connector_handoff": connector_handoff,
            "resource_inventory": resources,
            "constraints": {
                "write_scope": "native connector folder, its tests, and new org __init__.py only",
                "agent_must_not_run_final_verification": True,
                "focused_test_command": focused_test,
                "unit_test_rule": (
                    "Assert Pint units by semantic equality; do not assert their display string."
                ),
                "task_rule": (
                    "Preserve each validated task target as target_schema when the TimeNet task "
                    "supports it, and cover that field in connector tests."
                ),
                "deterministic_owner": [
                    "connector tests",
                    "TimeF build",
                    "TimeNet.load",
                    "raw-to-TimeF read-back verification",
                ],
            },
        }

    @staticmethod
    def _resource_inventory(
        profile: dict[str, Any], discovery_metadata: dict[str, Any]
    ) -> list[dict[str, Any]]:
        raw = profile.get("files")
        if not isinstance(raw, list):
            raw = discovery_metadata.get("resources", [])
        if not isinstance(raw, list):
            raise ValueError("dataset profile has no resource inventory")
        if len(raw) > _MAX_INVENTORY_ENTRIES:
            raise ValueError("resource inventory exceeds its 10,000-entry bound")
        inventory = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("resource inventory contains a non-object entry")
            variables = item.get("variables", [])
            inventory.append(
                {
                    key: item[key]
                    for key in (
                        "resource_id",
                        "asset_id",
                        "relative_path",
                        "logical_path",
                        "format",
                        "size_bytes",
                        "sha256",
                        "content_sha256",
                    )
                    if key in item
                }
                | {
                    "variables": [
                        {
                            key: variable[key]
                            for key in ("name", "shape", "dtype")
                            if key in variable
                        }
                        for variable in variables
                        if isinstance(variable, dict)
                    ]
                }
            )
        return inventory

    @staticmethod
    def _verified_source_root(job: OnboardingJob, profile: dict[str, Any]) -> Path:
        root = Path(job.source.local_path or "").expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"verified source root does not exist: {root}")
        source = profile.get("source", {})
        profiled = source.get("path") if isinstance(source, dict) else None
        if isinstance(profiled, str):
            candidate = Path(profiled).expanduser().resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                pass
            else:
                if candidate.is_dir():
                    return candidate
        return root

    def _prompt(self, handoff: Path, worktree: Path) -> str:
        focused_test = self._focused_test_command(worktree)
        test_instruction = (
            " Before returning, run the focused connector test command supplied in the handoff "
            "and fix every failure."
            if focused_test
            else ""
        )
        return (
            "Implement the approved native TimeNet connector described by "
            f"{handoff}. The current user explicitly approved this connector implementation; "
            "the persisted validated semantic specification and any human-approved implementation "
            "override in that handoff are the completed design gate. Read AGENTS.md, then read and "
            "follow .agents/skills/add-dataset-connector/SKILL.md and its referenced connector "
            "anatomy completely. Do not redesign or reinterpret the supplied semantics. Work only "
            f"inside the detached TimeNet worktree {worktree}. Create only the native connector "
            "folder, relevant fixture-based tests, and a new org namespace __init__.py if required. "
            "Do not edit core TimeNet, documentation, lockfiles, CI, or unrelated connectors. You "
            "may run focused tests while implementing, but the caller independently owns the "
            "authoritative connector test, TimeF build, TimeNet.load, and read-back verification. "
            "In tests, compare Pint units by semantic equality rather than their display string."
            " Preserve every validated named task target as target_schema when supported, and "
            "assert it in the focused tests."
            f"{test_instruction}"
        )

    def _focused_test_command(self, worktree: Path) -> list[str] | None:
        if self.config.focused_test_command is None:
            return None
        replacements = {"timenet_worktree": str(worktree)}
        return [
            part.format_map(replacements) for part in self.config.focused_test_command
        ]

    def _connector_path(self) -> str:
        org, name = self.config.dataset_id.split("/", 1)
        return (
            "packages/timenet-connectors/src/timenet_connectors/datasets/"
            f"{org.lower()}/{name.replace('-', '_').lower()}"
        )

    @staticmethod
    def _validate_changed_paths(
        worktree: Path, paths: list[dict[str, str]], connector_path: str
    ) -> None:
        if not paths:
            raise ConnectorAgentError(
                "coding agent produced no connector changes", {"paths": []}
            )
        org_path = connector_path.rsplit("/", 1)[0]
        allowed_namespace = f"{org_path}/__init__.py"
        invalid = [
            item["path"]
            for item in paths
            if not (
                item["path"].startswith(f"{connector_path}/")
                or item["path"] == allowed_namespace
            )
            or "D" in item["status"]
        ]
        if invalid:
            raise ConnectorAgentError(
                "coding agent changed files outside the connector-only boundary",
                {"paths": paths, "invalid_paths": invalid},
            )
        unsafe = [
            item["path"]
            for item in paths
            if (worktree / item["path"]).is_symlink()
            or not (worktree / item["path"]).is_file()
        ]
        if unsafe:
            raise ConnectorAgentError(
                "coding agent generated a non-regular connector path",
                {"paths": paths, "unsafe_paths": unsafe},
            )

    def _ensure_worktree(self, repository: Path, worktree: Path) -> None:
        if worktree.exists():
            head = self._git(worktree, "rev-parse", "HEAD").stdout.strip()
            if head != self.config.pinned_revision:
                raise ValueError(
                    "existing job TimeNet worktree is not at the pinned revision"
                )
            return
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self._git(
            repository,
            "worktree",
            "add",
            "--detach",
            str(worktree),
            self.config.pinned_revision,
        )

    def _changed_paths(self, worktree: Path) -> list[dict[str, str]]:
        raw = self._git(
            worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ).stdout
        result = []
        for entry in raw.split("\0"):
            if not entry:
                continue
            status, path = entry[:2], entry[3:]
            if "R" in status or "C" in status:
                raise ConnectorAgentError(
                    "connector agent may not rename or copy tracked paths",
                    {"status_entry": entry},
                )
            result.append({"status": status, "path": path})
        return sorted(result, key=lambda item: item["path"])

    @staticmethod
    def _agent_receipt(
        completed: subprocess.CompletedProcess[str], paths: list[dict[str, str]]
    ) -> dict[str, Any]:
        return {
            "argv": list(completed.args[:-1]),
            "returncode": completed.returncode,
            "stdout": completed.stdout[-_MAX_AGENT_OUTPUT_CHARS:],
            "stderr": completed.stderr[-_MAX_AGENT_OUTPUT_CHARS:],
            "changed_paths": paths,
        }

    @staticmethod
    def _read_object(path: Path) -> dict[str, Any]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"expected JSON object in {path}")
        return raw

    @staticmethod
    def _git(
        repository: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ("git", "-C", str(repository), *args),
            text=True,
            capture_output=True,
            check=False,
        )
        if check and completed.returncode:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        return completed


class ConnectorAgentError(RuntimeError):
    """Expose a bounded receipt when connector generation violates its contract."""

    def __init__(self, message: str, receipt: dict[str, Any]) -> None:
        super().__init__(message)
        self.receipt = receipt
