from __future__ import annotations

import sqlite3
import threading
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from data_sourcing.config import Settings
from data_sourcing.graph import DatasetScoutGraph, initial_state
from data_sourcing.models import ApprovalDecision, ApprovalRequest, CreateSourcingRun, RunStatus
from data_sourcing.storage import ArtifactStore, IdempotencyConflict, IdempotencyStore

_DEMO_REPOSITORY = "github.com/zhang-zengjie/robot-raw-collision-signals"


class RunConflict(ValueError):
    pass


class SourcingService:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(settings.runs_dir)
        self.idempotency = IdempotencyStore(settings.idempotency_path)
        self.checkpoint_connection = sqlite3.connect(
            settings.checkpoint_path,
            check_same_thread=False,
        )
        self.checkpointer = SqliteSaver(self.checkpoint_connection)
        self.checkpointer.setup()
        self.scout = DatasetScoutGraph(settings, self.checkpointer)
        self._graph_lock = threading.RLock()

    def close(self) -> None:
        self.scout.close()
        self.checkpoint_connection.close()
        self.idempotency.close()

    @staticmethod
    def _config(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

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

    def execute_run(self, run_id: str) -> None:
        run = self.artifacts.read_run(run_id)
        request = CreateSourcingRun(brief=run.brief, constraints=run.constraints)
        state = initial_state(
            run_id,
            request,
            allow_cached_demo=_DEMO_REPOSITORY in run.brief.casefold(),
        )
        try:
            with self._graph_lock:
                result = self.scout.graph.invoke(state, self._config(run_id))
        except Exception as exc:  # Boundary: persist a safe failure instead of exposing internals.
            state["status"] = RunStatus.FAILED.value
            state["errors"] = [f"Sourcing workflow failed: {type(exc).__name__}"]
            state["report_markdown"] = (
                "# Dataset sourcing failed\n\nRetry the run or inspect server logs.\n"
            )
            self.artifacts.persist(state)
            return
        self.artifacts.persist(result)

    def approve(self, run_id: str, approval: ApprovalRequest):
        run = self.artifacts.read_run(run_id)
        if run.status is not RunStatus.AWAITING_APPROVAL:
            if (
                run.status is RunStatus.APPROVED
                and approval.decision is ApprovalDecision.APPROVE
                and approval.candidate_id == run.recommended_candidate_id
            ):
                return run
            raise RunConflict("Run is not awaiting approval")
        try:
            with self._graph_lock:
                result = self.scout.graph.invoke(
                    Command(resume=approval.model_dump(mode="json", by_alias=True)),
                    self._config(run_id),
                )
        except Exception as exc:
            raise RunConflict(f"Approval could not be applied: {type(exc).__name__}") from exc
        return self.artifacts.persist(result)


__all__ = ["IdempotencyConflict", "RunConflict", "SourcingService"]
