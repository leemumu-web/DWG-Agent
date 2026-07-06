"""系统健康检查端点（spec §18.2 特性开关 + 运维接口）。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser
from app.core.config import settings
from app.schemas.common import ok

router = APIRouter()


@router.get("/health")
def get_system_health(request: Request, current_user: CurrentUser):
    """系统健康概览：数据库、Redis、ODA 状态、特性开关。"""
    from app.core.redis_client import get_redis

    redis_ok = get_redis() is not None

    features = {
        "agent": settings.agent_enabled,
        "dxf_pipeline": settings.dxf_pipeline_enabled,
        "cad_worker": settings.cad_worker_enabled,
    }

    return ok(
        {
            "status": "ok",
            "redis": redis_ok,
            "features": features,
            "storage_backend": settings.storage_backend,
        },
        request.state.request_id,
    )


@router.get("/health/oda")
def get_oda_health(request: Request, current_user: CurrentUser):
    """ODA File Converter 环境健康检查。

    返回 dwg_converter.framework.health_check() 的完整结果：
    - oda_found: ODA 可执行文件是否已定位
    - oda_executable: 实际使用的二进制路径
    - ezdxf_available: ezdxf 是否可用（仅解析阶段需要）
    - messages: 环境检查的详细日志
    """
    from dwg_converter.framework import health_check as oda_health_check

    status = oda_health_check()
    return ok(status.to_dict(), request.state.request_id)
