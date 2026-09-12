from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from data_sourcing.config import Settings
from data_sourcing.models import (
    ApprovalRequest,
    CreateSourcingRun,
    ErrorDetail,
    ErrorEnvelope,
    RunAccepted,
    SourcingManifest,
    SourcingRun,
)
from data_sourcing.service import IdempotencyConflict, RunConflict, SourcingService
from data_sourcing.storage import ArtifactUnavailable, RunNotFound


def _error(status: int, code: str, message: str, *, details=None) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorDetail(code=code, message=message, details=details)).model_dump(
        mode="json", by_alias=True
    )
    return JSONResponse(status_code=status, content=body)


def create_app(
    settings: Settings | None = None,
    service: SourcingService | None = None,
) -> FastAPI:
    configured_settings = settings or Settings()
    owns_service = service is None
    sourcing = service or SourcingService(configured_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_service:
            sourcing.close()

    app = FastAPI(
        title="Evidence-Complete Dataset Scout",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.sourcing = sourcing

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"location": list(error["loc"]), "message": error["msg"]} for error in exc.errors()
        ]
        return _error(422, "INVALID_REQUEST", "Request validation failed", details=details)

    @app.exception_handler(RunNotFound)
    async def run_not_found(_: Request, exc: RunNotFound) -> JSONResponse:
        return _error(404, "RUN_NOT_FOUND", str(exc))

    @app.exception_handler(ArtifactUnavailable)
    async def artifact_unavailable(_: Request, exc: ArtifactUnavailable) -> JSONResponse:
        return _error(409, "ARTIFACT_UNAVAILABLE", str(exc))

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_conflict(_: Request, exc: IdempotencyConflict) -> JSONResponse:
        return _error(409, "IDEMPOTENCY_CONFLICT", str(exc))

    @app.exception_handler(RunConflict)
    async def run_conflict(_: Request, exc: RunConflict) -> JSONResponse:
        return _error(409, "RUN_CONFLICT", str(exc))

    @app.post("/api/sourcing-runs", response_model=RunAccepted, status_code=202)
    def create_sourcing_run(
        body: CreateSourcingRun,
        background_tasks: BackgroundTasks,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ) -> RunAccepted:
        run_id, created = sourcing.create_run(body, idempotency_key)
        if created:
            background_tasks.add_task(sourcing.execute_run, run_id)
        run = sourcing.artifacts.read_run(run_id)
        return RunAccepted(
            run_id=run_id,
            status=run.status,
            status_url=f"/api/sourcing-runs/{run_id}",
        )

    @app.get("/api/sourcing-runs/{run_id}", response_model=SourcingRun)
    def get_sourcing_run(run_id: str) -> SourcingRun:
        return sourcing.artifacts.read_run(run_id)

    @app.post("/api/sourcing-runs/{run_id}/approvals", response_model=SourcingRun)
    def approve_sourcing_run(run_id: str, body: ApprovalRequest) -> SourcingRun:
        return sourcing.approve(run_id, body)

    @app.get("/api/sourcing-runs/{run_id}/report", response_class=PlainTextResponse)
    def get_report(run_id: str) -> PlainTextResponse:
        return PlainTextResponse(
            sourcing.artifacts.read_report(run_id),
            media_type="text/markdown; charset=utf-8",
        )

    @app.get("/api/sourcing-runs/{run_id}/manifest", response_model=SourcingManifest)
    def get_manifest(run_id: str) -> SourcingManifest:
        return sourcing.artifacts.read_manifest(run_id)

    return app
