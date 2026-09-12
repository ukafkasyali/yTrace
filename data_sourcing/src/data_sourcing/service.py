from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from data_sourcing.config import Settings
from data_sourcing.graph import DatasetScoutGraph, initial_state
from data_sourcing.models import (
    ApprovalDecision,
    ApprovalRequest,
    CreateSourcingRun,
    RequirementDefinition,
    RequirementPreviewRequest,
    RequirementsPreview,
    RunStatus,
    SourcingRun,
)
from data_sourcing.scoring import candidate_is_approvable
from data_sourcing.storage import (
    ApprovedSourceStore,
    ArtifactStore,
    IdempotencyConflict,
    IdempotencyStore,
    InvalidIdempotencyKey,
)

_DEMO_REPOSITORY = "github.com/zhang-zengjie/robot-raw-collision-signals"
LOGGER = logging.getLogger(__name__)


class RunConflict(ValueError):
    pass


class SourcingService:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(settings.runs_dir)
        self.approved_sources = ApprovedSourceStore(settings.approved_sources_path)
        self.idempotency = IdempotencyStore(settings.idempotency_path)
        self.checkpoint_connection = sqlite3.connect(
            settings.checkpoint_path,
            check_same_thread=False,
        )
        self.checkpointer = SqliteSaver(self.checkpoint_connection)
        self.checkpointer.setup()
        self.scout = DatasetScoutGraph(settings, self.checkpointer)
        self._graph_lock = threading.RLock()
        self._reconcile_approved_sources()

    def close(self) -> None:
        self.scout.close()
        self.checkpoint_connection.close()
        self.idempotency.close()
        self.approved_sources.close()

    def _reconcile_approved_sources(self) -> None:
        for manifest in self.artifacts.manifests():
            try:
                self.approved_sources.record_approval(manifest)
            except ValueError as exc:
                LOGGER.warning(
                    "Approved manifest %s could not be catalogued: %s",
                    manifest.run_id,
                    exc,
                )

    @staticmethod
    def _config(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    def _stream_graph(self, graph_input: Any, run_id: str) -> SourcingRun:
        latest = None
        for state in self.scout.graph.stream(
            graph_input,
            self._config(run_id),
            stream_mode="values",
        ):
            if state.get("run_id"):
                latest = self.artifacts.persist(state)
        if latest is None:
            snapshot = self.scout.graph.get_state(self._config(run_id))
            if not snapshot.values:
                raise RuntimeError("Graph produced no checkpointed state")
            latest = self.artifacts.persist(dict(snapshot.values))
        return latest

    def create_run(
        self,
        request: CreateSourcingRun,
        idempotency_key: str,
    ) -> tuple[str, bool]:
        proposed = str(uuid4())
        run_id, created = self.idempotency.claim(idempotency_key, request, proposed)
        if created:
            state = initial_state(
                run_id,
                request,
                allow_cached_demo=_DEMO_REPOSITORY in request.brief.casefold(),
            )
            self.artifacts.persist(state)
        return run_id, created

    def preview_requirements(
        self,
        request: RequirementPreviewRequest,
    ) -> RequirementsPreview:
        return self.scout.planner.preview(request)

    def execute_run(self, run_id: str) -> None:
        run = self.artifacts.read_run(run_id)
        requirements = None
        if run.requirements_confirmed:
            requirements = [
                RequirementDefinition.model_validate(
                    item.model_dump(exclude={"status", "evidence_ids"})
                )
                for item in run.requirements
            ]
        request = CreateSourcingRun(
            brief=run.brief,
            constraints=run.constraints,
            requirements=requirements,
        )
        state = initial_state(
            run_id,
            request,
            allow_cached_demo=_DEMO_REPOSITORY in run.brief.casefold(),
        )
        try:
            with self._graph_lock:
                self._stream_graph(state, run_id)
        except Exception as exc:  # Boundary: persist a safe failure instead of exposing internals.
            try:
                snapshot = self.scout.graph.get_state(self._config(run_id))
                failed_state = dict(snapshot.values) if snapshot.values else state
            except Exception:
                failed_state = state
            failed_state["status"] = RunStatus.FAILED.value
            failed_state["errors"] = [
                *failed_state.get("errors", []),
                f"Sourcing workflow failed: {type(exc).__name__}",
            ]
            failed_state["report_markdown"] = (
                "# Dataset sourcing failed\n\nRetry the run or inspect server logs.\n"
            )
            self.artifacts.persist(failed_state)
            return

    def approve(self, run_id: str, approval: ApprovalRequest) -> SourcingRun:
        run = self.artifacts.read_run(run_id)
        can_resume_for_feedback = (
            run.status is RunStatus.NEEDS_INPUT
            and approval.decision is ApprovalDecision.REJECT
            and run.feedback_allowed
        )
        if run.status is not RunStatus.AWAITING_APPROVAL and not can_resume_for_feedback:
            approved_candidate_id = run.approved_candidate_id or (
                run.manifest.candidate_id if run.manifest else None
            )
            if (
                run.status is RunStatus.APPROVED
                and approval.decision is ApprovalDecision.APPROVE
                and approval.candidate_id == approved_candidate_id
            ):
                if run.manifest:
                    self.approved_sources.record_approval(run.manifest)
                return run
            raise RunConflict("Run is not awaiting approval or reviewer feedback")
        selected = next(
            (
                item
                for item in run.assessments
                if item.candidate_id == approval.candidate_id
            ),
            None,
        )
        if (
            approval.decision is ApprovalDecision.REJECT
            and approval.candidate_id
            and selected is None
        ):
            raise RunConflict("Rejected candidate must be assessed in this run")
        if approval.decision is ApprovalDecision.APPROVE and (
            selected is None
            or not candidate_is_approvable(selected)
            or selected.candidate_id in run.excluded_candidate_ids
        ):
            raise RunConflict("Approved candidate must pass every mandatory gate")
        try:
            with self._graph_lock:
                completed = self._stream_graph(
                    Command(resume=approval.model_dump(mode="json", by_alias=True)),
                    run_id,
                )
        except Exception as exc:
            raise RunConflict(f"Approval could not be applied: {type(exc).__name__}") from exc
        if completed.manifest:
            self.approved_sources.record_approval(completed.manifest)
        return completed


__all__ = [
    "IdempotencyConflict",
    "InvalidIdempotencyKey",
    "RunConflict",
    "SourcingService",
]
