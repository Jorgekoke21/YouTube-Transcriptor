"""FastAPI application factory.

Run with: `uvicorn app.main:create_app --factory --reload --port 8000`.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.container import build_service
from app.core.errors import AppError, ErrorCode
from app.repositories.collections import CollectionRepository
from app.services.collection_service import CollectionService
from app.services.document_service import DocumentService

logger = logging.getLogger("app")


def create_app(
    settings: Settings | None = None, service: DocumentService | None = None, collections: CollectionService | None = None
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = FastAPI(title="YouTube to Document", version="1.0.0")
    if settings.ai_provider == "openai":
        logger.info(settings.openai_config_summary())
    else:
        logger.info("AI provider: %s (OpenAI not used)", settings.ai_provider)
    app.state.service = service or build_service(settings)
    app.state.collections = collections or CollectionService(app.state.service, CollectionRepository(app.state.service.repo.db))
    interrupted = app.state.service.repo.fail_interrupted()
    if interrupted:
        logger.info("Marked %s interrupted document(s) as failed", interrupted)
    interrupted = app.state.collections.repo.fail_interrupted()
    if interrupted:
        logger.info("Marked %s interrupted collection(s) as failed", interrupted)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["Content-Disposition"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ", ".join(".".join(str(p) for p in e.get("loc", [])[1:]) for e in exc.errors())
        err = AppError(ErrorCode.INVALID_REQUEST, f"Solicitud no válida ({fields})." if fields else None)
        return JSONResponse(status_code=400, content={"error": err.to_dict()})

    @app.exception_handler(Exception)
    async def unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"error": AppError(ErrorCode.INTERNAL_ERROR).to_dict()})

    app.include_router(router)
    return app
