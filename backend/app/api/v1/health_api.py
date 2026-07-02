from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.redis_client import redis_health
from app.db.session import db_health
from app.schemas.common import ok

router = APIRouter()


@router.get("/health")
def health(request: Request):
    components = {
        "api": {"status": "ok", "message": "backend-api is running."},
        "database": db_health(),
        "redis": redis_health(),
    }
    overall = all(c["status"] == "ok" for c in components.values())
    return ok(
        {
            "status": "ok" if overall else "degraded",
            "service": "backend-api",
            "components": components,
        },
        request.state.request_id,
    )
