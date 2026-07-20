"""Operator tools backed by the isolated Excel Final Stage."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.modules.excel_processing.stage_adapter import (
    ExcelFinalIntegrationError,
    lookup_excel_final_weight,
)
from app.modules.identity.interface import CurrentUser
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import service_unavailable

router = APIRouter()


@router.get("/weights/lookup")
def lookup_weight(
    request: Request,
    current_user: CurrentUser,
    spec: str = Query(..., min_length=1, description="钢材规格, e.g. L50x5, φ60*3.5, PL10*200"),
):
    """通过 hardware_handbook MySQL 查询钢材比重（kg/m）。"""
    try:
        weight, source = lookup_excel_final_weight(spec)
        return ok(
            {"spec": spec, "weight_kg_per_m": weight, "source": source},
            request.state.request_id,
        )
    except ExcelFinalIntegrationError as exc:
        raise service_unavailable(
            "EXCEL_FINAL_UNAVAILABLE",
            "Excel Final 比重查询暂不可用。",
        ) from exc


__all__ = ["router"]
