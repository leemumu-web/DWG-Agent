"""Authoritative, confirmed-milestone progress for DWG-to-DXF."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.cad_processing.dwg_to_dxf.contracts import ERROR_CODE_SOURCE_MISSING
from app.modules.jobs.interface import make_event
from app.platform.config.constants import JOB_RUNNING


@dataclass(frozen=True)
class DwgProgressPhase:
    code: str
    progress: int
    label: str
    message: str
    indeterminate: bool = False


CLAIMED = DwgProgressPhase(
    code="claimed",
    progress=5,
    label="任务已领取",
    message="转换任务已由 CAD 处理服务领取",
)
ODA_CONVERTING = DwgProgressPhase(
    code="oda_converting",
    progress=20,
    label="ODA 转换中",
    message="源 DWG 已就绪，ODA 正在转换",
    indeterminate=True,
)
ODA_RESULT_READY = DwgProgressPhase(
    code="oda_result_ready",
    progress=70,
    label="ODA 已返回",
    message="ODA 已返回该图纸的转换结果",
)
PERSISTING = DwgProgressPhase(
    code="persisting",
    progress=85,
    label="结果登记中",
    message="DXF 已生成并完成结构检查，正在登记结果",
)
COMPLETED = DwgProgressPhase(
    code="completed",
    progress=100,
    label="已完成",
    message="DXF 转换和结果登记已完成",
)


def phase_data(phase: DwgProgressPhase, **extra: object) -> dict[str, object]:
    return {
        "phase": phase.code,
        "phase_label": phase.label,
        "indeterminate": phase.indeterminate,
        "progress_basis": "confirmed_milestone",
        **extra,
    }


def phase_event(
    phase: DwgProgressPhase,
    *,
    step_name: str | None = None,
    message: str | None = None,
    **extra: object,
) -> dict[str, object]:
    return make_event(
        type_="progress",
        status=JOB_RUNNING,
        progress=phase.progress,
        step_name=step_name,
        message=message or phase.message,
        **phase_data(phase, **extra),
    )


def safe_convert_result_metadata(result: Any) -> dict[str, object]:
    """Keep operational facts while excluding temporary paths and subprocess data."""
    duration = getattr(result, "duration", 0.0)
    try:
        duration_seconds = round(max(0.0, float(duration)), 3)
    except (TypeError, ValueError):
        duration_seconds = 0.0
    return {
        "success": bool(getattr(result, "success", False)),
        "duration_seconds": duration_seconds,
    }


def safe_failure_message(error_code: str) -> str:
    if error_code == ERROR_CODE_SOURCE_MISSING:
        return "源 DWG 文件不存在或已删除，请重新上传后再处理。"
    return "DWG 转 DXF 未完成。系统已完成自动重试，请检查源图纸是否损坏或格式不受支持。"


__all__ = [
    "CLAIMED",
    "COMPLETED",
    "ODA_CONVERTING",
    "ODA_RESULT_READY",
    "PERSISTING",
    "DwgProgressPhase",
    "phase_data",
    "phase_event",
    "safe_convert_result_metadata",
    "safe_failure_message",
]
