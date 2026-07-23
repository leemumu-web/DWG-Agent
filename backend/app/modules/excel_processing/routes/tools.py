"""Operator tools backed by the isolated Excel Final Stage."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.modules.excel_processing.schemas import HandbookCategory
from app.modules.excel_processing.stage_adapter import (
    ExcelFinalIntegrationError,
    lookup_excel_final_weight,
)
from app.modules.identity.interface import CurrentUser
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException, service_unavailable

router = APIRouter()


@router.get("/weights/lookup")
def lookup_weight(
    request: Request,
    current_user: CurrentUser,
    category: HandbookCategory = Query(..., description="稳定的五金手册类别"),
    spec: str = Query(..., min_length=1, description="钢材规格, e.g. L50x5, φ60*3.5, PL10*200"),
    material: str | None = Query(None, description="D 系列必须提供材质"),
):
    """通过 hardware_handbook MySQL 查询钢材比重（kg/m）。"""
    try:
        result = lookup_excel_final_weight(
            category=category.value,
            spec=spec,
            material=material,
        )
        return ok(
            {
                "category": result.category,
                "spec": spec,
                "normalized_spec": result.normalized_spec,
                "material": result.material,
                "weight_kg_per_m": result.weight_kg_per_m,
                "source": result.source,
                "status": result.status,
            },
            request.state.request_id,
        )
    except ValueError as exc:
        raise AppHTTPException(
            422,
            "INVALID_HANDBOOK_LOOKUP",
            str(exc),
        ) from exc
    except ExcelFinalIntegrationError as exc:
        raise service_unavailable(
            "EXCEL_FINAL_UNAVAILABLE",
            "Excel Final 比重查询暂不可用。",
        ) from exc


__all__ = ["router"]
