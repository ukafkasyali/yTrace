from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Request
from fastapi import Path as ApiPath
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .jobs import IngestionJob, IngestionJobConflict, IngestionJobNotFound
from .service import (
    ApprovedSourceResolutionError,
    CreateIngestion,
    HttpApprovedSourceResolver,
    IngestionService,
)

LOGGER = logging.getLogger(__name__)


def _error(status: int, code: str, message: str, *, details=None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": details,
            }
        },
    )


def create_app(service: IngestionService | None = None) -> FastAPI:
    owns_service = service is None
    ingestion = service or IngestionService(
        data_dir=Path(os.environ.get("INGESTION_DATA_DIR", "var/ingestion")),
        resolver=HttpApprovedSourceResolver(
            os.environ.get("INGESTION_SOURCING_API_URL", "http://127.0.0.1:8001")
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_service:
            ingestion.close()

    app = FastAPI(title="Approved Dataset Ingestion", version="0.1.0", lifespan=lifespan)
    app.state.ingestion = ingestion

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

    @app.exception_handler(IngestionJobConflict)
    async def ingestion_conflict(_: Request, exc: IngestionJobConflict) -> JSONResponse:
        return _error(409, "INGESTION_CONFLICT", str(exc))

    @app.exception_handler(IngestionJobNotFound)
    async def ingestion_not_found(_: Request, exc: IngestionJobNotFound) -> JSONResponse:
        return _error(404, "INGESTION_NOT_FOUND", str(exc))

    @app.exception_handler(ApprovedSourceResolutionError)
    async def source_unavailable(_: Request, exc: ApprovedSourceResolutionError) -> JSONResponse:
        return _error(422, "APPROVED_SOURCE_UNAVAILABLE", str(exc))

    @app.exception_handler(Exception)
    async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("Unhandled ingestion API error", exc_info=exc)
        return _error(500, "INTERNAL_ERROR", "An unexpected server error occurred")

    @app.post("/api/ingestions", response_model=IngestionJob, status_code=202)
    def create_ingestion(body: CreateIngestion) -> IngestionJob:
        job, _ = ingestion.create(body)
        return job

    @app.get("/api/ingestions/{ingestion_id}", response_model=IngestionJob)
    def get_ingestion(
        ingestion_id: Annotated[str, ApiPath(pattern=r"^[0-9a-f-]{36}$")],
    ) -> IngestionJob:
        return ingestion.jobs.get(ingestion_id)

    return app
