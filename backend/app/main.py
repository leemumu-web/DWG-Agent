from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import AppHTTPException
from app.core.logger import configure_logging
from app.core.redis_client import close_redis, get_redis, redis_health
from app.db.session import db_health
from app.schemas.common import meta, ok

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: eagerly probe Redis so the log shows connected/unavailable early
    get_redis()
    yield
    # Shutdown: clean up Redis connection pool
    close_redis()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/health")
def root_health(request: Request):
    components = {
        "api": {"status": "ok", "message": "dwg-agent-backend is running."},
        "database": db_health(),
        "redis": redis_health(),
    }
    overall = all(c["status"] == "ok" for c in components.values())
    return ok(
        {
            "status": "ok" if overall else "degraded",
            "service": "dwg-agent-backend",
            "components": components,
        },
        request.state.request_id,
    )


app.include_router(api_router, prefix=settings.api_v1_prefix)
