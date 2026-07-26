"""Safe, operator-readable projection of Job and JobStep state."""

from __future__ import annotations

import re
from datetime import datetime
from math import isfinite
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.jobs.models import Job, JobStep

_STEP_LABELS = {
    "download_source_dwg": "读取源 DWG",
    "download_source_dxf": "读取源 DXF",
    "run_oda_convert": "CAD 格式转换",
    "persist_dxf_result": "登记 DXF 结果",
    "persist_dwg_result": "登记 DWG 结果",
    "extract_dxf_materials": "提取材料数据",
    "persist_excel_result": "登记 Excel 结果",
    "stub_execute": "框架检查",
}
_STATUS_LABELS = {
    "pending": "待处理",
    "queued": "排队中",
    "running": "处理中",
    "validating": "校验中",
    "waiting_cad_worker": "等待 CAD 处理",
    "succeeded": "已完成",
    "failed": "处理失败",
    "cancelled": "已取消",
}
_DETAIL_LABELS = {
    "file_id": "源文件编号",
    "source_file_id": "源文件编号",
    "version": "目标版本",
    "audit": "ODA 审计",
    "timeout": "超时秒数",
    "retries": "自动重试次数",
    "batch_size": "批组文件数",
    "batch_group": "转换批组",
    "source_size_bytes": "源文件字节数",
    "dxf_size": "DXF 字节数",
    "output_size_bytes": "输出字节数",
    "dxf_file_id": "DXF 文件编号",
    "analysis_result_id": "分析结果编号",
    "total_entities": "DXF 实体总数",
    "entity_counts": "DXF 实体分类",
    "duration_seconds": "耗时秒数",
    "completed_shards": "已完成分片",
    "total_shards": "分片总数",
}
_TECHNICAL_TEXT = re.compile(
    r"(traceback|sqlalchemy|pymysql|operationalerror|exception|"
    r"(?:^|[\s\"'])/(?:tmp|app|home|var)/|[a-zA-Z]:\\\\)",
    re.IGNORECASE,
)
_SAFE_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$", re.IGNORECASE)
_SAFE_DETAIL_KEY = re.compile(r"^[\w .:+-]{1,80}$")


def _duration_seconds(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    if started_at is None or finished_at is None:
        return None
    return round(max(0.0, (finished_at - started_at).total_seconds()), 3)


def _safe_text(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.strip().split())
    if not text or len(text) > 300 or _TECHNICAL_TEXT.search(text):
        return fallback
    return text


def _safe_token(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    token = value.strip()
    if not _SAFE_TOKEN.fullmatch(token):
        return fallback
    return token


def _safe_detail_value(value: object, *, allow_mapping: bool = True) -> tuple[bool, object]:
    if isinstance(value, bool):
        return True, value
    if isinstance(value, int):
        return True, value
    if isinstance(value, float):
        return (True, value) if isfinite(value) else (False, None)
    if isinstance(value, str):
        text = " ".join(value.strip().split())
        if not text or len(text) > 160 or _TECHNICAL_TEXT.search(text):
            return False, None
        return True, text
    if isinstance(value, dict) and allow_mapping and len(value) <= 50:
        safe_mapping: dict[str, object] = {}
        for nested_key, nested_value in value.items():
            if (
                not isinstance(nested_key, str)
                or not _SAFE_DETAIL_KEY.fullmatch(nested_key)
                or _TECHNICAL_TEXT.search(nested_key)
            ):
                continue
            safe, projected = _safe_detail_value(nested_value, allow_mapping=False)
            if safe:
                safe_mapping[nested_key] = projected
        return True, safe_mapping
    return False, None


def _safe_details(*payloads: dict[str, Any] | None) -> list[dict[str, object]]:
    details: list[dict[str, object]] = []
    seen: set[str] = set()
    for payload in payloads:
        for key, value in (payload or {}).items():
            if key not in _DETAIL_LABELS or key in seen:
                continue
            safe, projected = _safe_detail_value(value)
            if not safe:
                continue
            seen.add(key)
            details.append({"key": key, "label": _DETAIL_LABELS[key], "value": projected})
    return details


def build_job_diagnostics(db: Session, job: Job) -> dict[str, object]:
    """Build a bounded view with no raw paths, logs, or arbitrary JSON."""
    progress_data = job.progress_data or {}
    fallback_label = _STATUS_LABELS.get(job.status, "状态待确认")
    phase_label = _safe_text(progress_data.get("phase_label"), fallback_label)
    phase_message = _safe_text(progress_data.get("message"), phase_label)
    steps = list(
        db.scalars(
            select(JobStep)
            .where(JobStep.job_id == job.id, JobStep.attempt == job.attempt)
            .order_by(JobStep.id)
        ).all()
    )
    logs = []
    for step in steps:
        step_label = _STEP_LABELS.get(step.step_name, "任务处理步骤")
        logs.append(
            {
                "step": step.step_name,
                "label": step_label,
                "status": step.status,
                "status_label": _STATUS_LABELS.get(step.status, step.status),
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "duration_seconds": _duration_seconds(step.started_at, step.finished_at),
                "details": _safe_details(step.input_json, step.output_json),
                "message": (
                    _safe_text(step.error_message, f"{step_label}未完成，请核对源文件后重试。")
                    if step.status == "failed"
                    else f"{step_label}已完成"
                ),
            }
        )
    return {
        "job_id": job.id,
        "attempt": job.attempt,
        "task_type": job.task_type,
        "status": job.status,
        "status_label": fallback_label,
        "progress": max(0, min(100, job.progress or 0)),
        "current_phase": {
            "code": _safe_token(progress_data.get("phase"), job.status),
            "label": phase_label,
            "message": phase_message,
            "indeterminate": bool(progress_data.get("indeterminate", False)),
            "basis": _safe_token(
                progress_data.get("progress_basis"),
                "confirmed_state",
            ),
        },
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "duration_seconds": _duration_seconds(job.started_at, job.finished_at),
        # Keep the historical key for API compatibility. These are structured,
        # sanitized step records, never server log lines.
        "logs": logs,
        "message": "仅展示可安全核验的任务阶段，不包含服务器原始日志。",
    }


__all__ = ["build_job_diagnostics"]
