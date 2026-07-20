"""框架集成适配层：将 dwg_converter 的错误/结果映射为 FastAPI 兼容格式。

本模块是 dwg_converter 与 complete_framework 之间的桥梁：
- 不依赖 FastAPI/pydantic（保持 converter 零硬依赖），只使用 stdlib dataclasses。
- 调用方（backend/app/services/dxf_service.py）直接用本模块的返回值构造
  FastAPI Response，无需手动做错误码映射。

用法::

    from dwg_converter.framework import (
        HealthStatus, health_check, to_api_dict, ERROR_CODES
    )

    # 健康检查（FastAPI startup / GET /health/oda）
    status = health_check()
    if not status.healthy:
        raise service_unavailable(status.error_code, status.message)

    # 结果转 API 字典
    result = convert_file(source, target_dir)
    body = to_api_dict(result)  # {"success": true, "data": {...}} 或 {"success": false, "error": {...}}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .check_env import check_environment, EnvironmentStatus
from .engines.oda_converter import ConvertResult, BatchResult, OdaConvertError

# ---- error codes (match backend/app/platform/http/exceptions.py pattern) ---

# 所有错误码使用 UPPER_SNAKE_CASE，与服务端 AppHTTPException 对齐。
ERROR_CODES = {
    # 环境错误（→ 503 service_unavailable）
    "ODA_NOT_FOUND": "ODA_NOT_FOUND",
    "XVFB_NOT_FOUND": "XVFB_NOT_FOUND",
    # 转换错误（→ job 内 error_code，不直接作为 HTTP 状态码）
    "DXF_CONVERSION_FAILED": "DXF_CONVERSION_FAILED",
    "DXF_SOURCE_MISSING": "DXF_SOURCE_MISSING",
    "DXF_TIMEOUT": "DXF_TIMEOUT",
}

# ---- health check -----------------------------------------------------------


@dataclass
class HealthStatus:
    """ODA 环境健康状态，可直接序列化成 API 响应。"""

    healthy: bool
    oda_found: bool
    oda_executable: str | None
    ezdxf_available: bool
    messages: list[str] = field(default_factory=list)
    # 不健康时的 error_code（对齐 service_unavailable 的 code 参数）。
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "oda_found": self.oda_found,
            "oda_executable": self.oda_executable,
            "ezdxf_available": self.ezdxf_available,
            "messages": self.messages,
            "error_code": self.error_code,
        }


def health_check() -> HealthStatus:
    """检查 ODA 环境是否就绪，返回框架可直接使用的 HealthStatus。

    供 FastAPI startup 事件或 /health/oda 端点使用。
    不抛异常——所有状态都体现在返回值中。
    """
    raw: EnvironmentStatus = check_environment()

    if raw.oda_found:
        return HealthStatus(
            healthy=True,
            oda_found=True,
            oda_executable=str(raw.oda_executable),
            ezdxf_available=raw.ezdxf_available,
            messages=raw.messages,
        )

    # 分析缺失原因，映射到具体 error_code
    oda_msgs = [m for m in raw.messages if "ODA" in m.upper() or "可执行" in m]
    xvfb_msgs = [m for m in raw.messages if "xvfb" in m.lower()]

    if oda_msgs:
        error_code = ERROR_CODES["ODA_NOT_FOUND"]
        detail = "ODA File Converter 未找到。请安装后放入 tools/oda/ 或设置 $ODA_HOME。"
    else:
        error_code = ERROR_CODES["ODA_NOT_FOUND"]
        detail = "ODA 环境检查未通过。"

    return HealthStatus(
        healthy=False,
        oda_found=False,
        oda_executable=None,
        ezdxf_available=raw.ezdxf_available,
        messages=raw.messages,
        error_code=error_code,
    )


# ---- result formatting ------------------------------------------------------


def to_api_dict(result: ConvertResult) -> dict[str, Any]:
    """把 ConvertResult 格式化为框架兼容的 API 字典。

    成功: {"success": true, "data": {"source": ..., "target": ..., "duration": ...}}
    失败: {"success": false, "error": {"code": ..., "message": ...}}

    调用方可直接包一层 ok() 返回，或按需补充 job_id/meta 等字段。
    """
    base = result.to_dict()
    if result.success:
        return {"success": True, "data": base}
    return {
        "success": False,
        "error": {
            "code": _failure_code(result),
            "message": result.error or "转换失败",
        },
    }


def to_batch_api_dict(batch: BatchResult) -> dict[str, Any]:
    """BatchResult → API 字典。"""
    return {
        "success": batch.all_success,
        "data": batch.to_dict(),
    }


def _failure_code(result: ConvertResult) -> str:
    """根据 ConvertResult.error 推断合适的 error_code。"""
    err = (result.error or "").lower()
    if "timeout" in err or "超时" in err:
        return ERROR_CODES["DXF_TIMEOUT"]
    if "not found" in err or "不存在" in err:
        return ERROR_CODES["DXF_SOURCE_MISSING"]
    return ERROR_CODES["DXF_CONVERSION_FAILED"]


# ---- convenience: wrap convert_file with health pre-check -------------------


def convert_with_health_check(
    source: str,
    target_dir: str,
    *,
    version: str | None = None,
    audit: bool | None = None,
    timeout: int | None = None,
    retries: int | None = None,
) -> tuple[ConvertResult | None, HealthStatus | None]:
    """先检查环境，健康则执行转换。不健康时返回 (None, HealthStatus)。

    使用方式::

        result, health = convert_with_health_check(source, target_dir)
        if health is not None:
            raise service_unavailable(health.error_code, ...)
        # result is guaranteed not None here
    """
    status = health_check()
    if not status.healthy:
        return None, status

    from .service import convert_file

    result = convert_file(
        source=source,
        target_dir=target_dir,
        version=version,
        audit=audit,
        timeout=timeout,
        retries=retries,
    )
    return result, None
