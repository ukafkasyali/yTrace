from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from data_sourcing.config import Settings
from data_sourcing.models import (
    ApprovalRequest,
    ApprovedSourceDetail,
    ApprovedSourcePage,
    CreateSourcingRun,
    ErrorDetail,
    ErrorEnvelope,
    RequirementPreviewRequest,
    RequirementsPreview,
    RunAccepted,
    SourceKind,
    SourcingManifest,
    SourcingRun,
)
from data_sourcing.service import (
    IdempotencyConflict,
    InvalidIdempotencyKey,
    RunConflict,
    SourcingService,
)
from data_sourcing.storage import ApprovedSourceNotFound, ArtifactUnavailable, RunNotFound

LOGGER = logging.getLogger(__name__)


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

    @app.exception_handler(ApprovedSourceNotFound)
    async def approved_source_not_found(_: Request, exc: ApprovedSourceNotFound) -> JSONResponse:
        return _error(404, "APPROVED_SOURCE_NOT_FOUND", str(exc))

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_conflict(_: Request, exc: IdempotencyConflict) -> JSONResponse:
        return _error(409, "IDEMPOTENCY_CONFLICT", str(exc))

    @app.exception_handler(InvalidIdempotencyKey)
    async def invalid_idempotency_key(_: Request, exc: InvalidIdempotencyKey) -> JSONResponse:
        return _error(422, "INVALID_IDEMPOTENCY_KEY", str(exc))

    @app.exception_handler(RunConflict)
    async def run_conflict(_: Request, exc: RunConflict) -> JSONResponse:
        return _error(409, "RUN_CONFLICT", str(exc))

    @app.exception_handler(Exception)
    async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled dataset sourcing API error", exc_info=exc)
        return _error(500, "INTERNAL_ERROR", "An unexpected server error occurred")

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

    @app.post(
        "/api/sourcing-requirement-previews",
        response_model=RequirementsPreview,
    )
    def preview_sourcing_requirements(
        body: RequirementPreviewRequest,
    ) -> RequirementsPreview:
        return sourcing.preview_requirements(body)

    @app.get("/api/sourcing-runs/{run_id}", response_model=SourcingRun)
    def get_sourcing_run(run_id: str) -> SourcingRun:
        return sourcing.get_run(run_id)

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

    @app.get("/api/approved-sources", response_model=ApprovedSourcePage)
    def list_approved_sources(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
        source_kind: Annotated[SourceKind | None, Query(alias="sourceKind")] = None,
        query: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    ) -> ApprovedSourcePage:
        return sourcing.approved_sources.list(
            page=page,
            page_size=page_size,
            source_kind=source_kind,
            query=query,
        )

    @app.get("/api/approved-sources/{approved_source_id}", response_model=ApprovedSourceDetail)
    def get_approved_source(approved_source_id: str) -> ApprovedSourceDetail:
        return sourcing.approved_sources.get(approved_source_id)

    @app.get(
        "/api/approved-sources/{approved_source_id}/manifest",
        response_model=SourcingManifest,
    )
    def get_approved_source_manifest(approved_source_id: str) -> SourcingManifest:
        return sourcing.approved_sources.read_manifest(approved_source_id)

    @app.delete("/api/approved-sources/{approved_source_id}", status_code=204)
    def delete_approved_source(approved_source_id: str) -> None:
        sourcing.approved_sources.delete(approved_source_id)

    return app
