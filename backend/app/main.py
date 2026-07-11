from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import AppHTTPException
from app.core.logger import configure_logging
from app.db.session import db_health
from app.schemas.common import meta, ok
from app.services.storage_service import storage_health

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialise DB schema/seed data
    try:
        from app.db.init_db import init_db

        init_db()
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "Database initialisation failed — may already be initialised or MySQL is unreachable."
        )
    yield


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
    # Disable interactive docs and OpenAPI schema in production-like environments
    # to prevent unauthorised API discovery (see pen-test finding BUG-21).
    # Enabled only when explicitly in development mode OR debug is on.
    docs_url="/docs" if (settings.app_env == "development" or settings.debug) else None,
    redoc_url="/redoc" if (settings.app_env == "development" or settings.debug) else None,
    openapi_url="/openapi.json" if (settings.app_env == "development" or settings.debug) else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID", f"req_{uuid4().hex}")
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(AppHTTPException)
async def app_http_exception_handler(request: Request, exc: AppHTTPException):
    detail = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {"code": "ERROR", "message": str(exc.detail), "details": {}}
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": detail, "meta": meta(request.state.request_id)},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {"code": "HTTP_ERROR", "message": str(exc.detail), "details": {}},
            "meta": meta(getattr(request.state, "request_id", "unknown")),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed.",
                "details": {"errors": jsonable_encoder(exc.errors())},
            },
            "meta": meta(getattr(request.state, "request_id", "unknown")),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all handler — logs the full traceback but never leaks it to clients."""
    logging.getLogger(__name__).exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Internal server error." if not settings.debug else str(exc),
                "details": {},
            },
            "meta": meta(getattr(request.state, "request_id", "unknown")),
        },
    )


@app.get("/health")
def root_health(request: Request):
    """Lightweight health check — no infrastructure details exposed."""
    return ok({"status": "ok"}, request.state.request_id)


@app.get("/health/ready")
def readiness_health(request: Request, response: Response):
    """Readiness probe for the authoritative database and configured storage."""
    database = db_health()
    storage = storage_health()
    ready = database["status"] == "ok" and storage["status"] == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ok(
        {
            "status": "ok" if ready else "error",
            "database": {
                "status": database["status"],
                "message": (
                    "Database is reachable."
                    if database["status"] == "ok"
                    else "Database is unavailable."
                ),
            },
            "storage": {
                "status": storage["status"],
                "message": (
                    "Storage is reachable."
                    if storage["status"] == "ok"
                    else "Storage is unavailable."
                ),
            },
        },
        request.state.request_id,
    )


app.include_router(api_router, prefix=settings.api_v1_prefix)
