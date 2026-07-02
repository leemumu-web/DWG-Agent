from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.schemas.common import ok

router = APIRouter()


@router.get("/health")
def health(request: Request):
    return ok({"status": "ok", "service": "backend-api"}, request.state.request_id)
