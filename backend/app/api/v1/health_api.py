from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas.common import ok

router = APIRouter()


@router.get("/health")
def health(request: Request):
    """Lightweight health check — no infrastructure details exposed."""
    return ok({"status": "ok"}, request.state.request_id)
