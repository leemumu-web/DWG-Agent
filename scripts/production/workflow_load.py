#!/usr/bin/env python3
"""Drive and verify concurrent production workflows through the public HTTP API."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import os
import secrets
import sys
import time
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "password",
        "access_token",
        "refresh_token",
        "token",
        "dsn",
        "database_url",
        "minio_secret_key",
        "mysql_password",
    }
)


class CountConservationError(ValueError):
    """A production stage silently lost or duplicated input items."""


class LoadApiError(RuntimeError):
    """A public API request failed with a reportable production error."""

    def __init__(
        self,
        *,
        stage: str,
        status_code: int | None,
        code: str,
        message: str,
        request_id: str | None = None,
    ) -> None:
        super().__init__(f"{stage}：{message}")
        self.stage = stage
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class LoadFixture:
    excel: Path
    dwg_files: tuple[Path, ...]

    def validate(self) -> None:
        if not self.excel.is_file():
            raise ValueError(f"Excel 测试样本不存在：{self.excel}")
        if self.excel.suffix.casefold() not in {".xls", ".xlsx"}:
            raise ValueError("Excel 测试样本必须是 .xls 或 .xlsx")
        if not self.dwg_files:
            raise ValueError("至少需要一张 DWG 测试图纸")
        parents = {path.parent.resolve() for path in self.dwg_files}
        if len(parents) != 1:
            raise ValueError("DWG 测试图纸必须来自同一个文件夹")
        missing = [str(path) for path in self.dwg_files if not path.is_file()]
        if missing:
            raise ValueError(f"DWG 测试样本不存在：{missing[0]}")
        invalid = [path.name for path in self.dwg_files if path.suffix.casefold() != ".dwg"]
        if invalid:
            raise ValueError(f"测试图纸不是 DWG：{invalid[0]}")
        names = [path.name.casefold() for path in self.dwg_files]
        if len(names) != len(set(names)):
            raise ValueError("DWG 测试样本包含忽略大小写后重名的文件")


@dataclass(frozen=True, slots=True)
class ProjectCounts:
    source_dwg: int
    converted_dxf: int
    classification_input: int
    classified: int
    review_required: int
    unreadable: int
    split_input: int
    split_auto_accepted: int
    split_manual_review: int
    split_failed: int


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    name: str
    succeeded: bool
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class SplitArchiveInspection:
    dxf_count: int
    original_length_count: int
    allowance_extended_count: int
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectRunResult:
    name: str
    succeeded: bool
    workflow_id: int | None
    counts: ProjectCounts | None
    archive_dxf_count: int
    job_attempts: dict[str, dict[str, int]]
    request_ids: tuple[str, ...]
    elapsed_seconds: float
    phase_seconds: dict[str, float]
    error_code: str | None = None
    error_message: str | None = None
    error_stage: str | None = None
    http_status: int | None = None

    def to_report(self) -> dict[str, Any]:
        return asdict(self)


def parse_positive_int_list(value: str) -> list[int]:
    """Parse an ordered, de-duplicated comma-separated positive integer list."""
    try:
        parsed = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("并发档位必须是逗号分隔的正整数") from exc
    if not parsed or any(item <= 0 for item in parsed):
        raise ValueError("并发档位必须至少包含一个正整数")
    return list(dict.fromkeys(parsed))


def percentile(samples: Sequence[float], percent: float) -> float:
    """Return a linearly interpolated percentile."""
    if not samples:
        raise ValueError("百分位样本不能为空")
    if percent < 0 or percent > 100:
        raise ValueError("百分位必须位于 0 到 100")
    ordered = sorted(float(sample) for sample in samples)
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def redact_secrets(value: Any, *, key: str | None = None) -> Any:
    """Recursively remove credentials and tokens from report-shaped values."""
    if key is not None and key.casefold() in SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_secrets(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    return value


def _require_non_negative(counts: ProjectCounts) -> None:
    for field, value in asdict(counts).items():
        if value < 0:
            raise CountConservationError(f"{field} 不能是负数")


def validate_project_counts(counts: ProjectCounts) -> None:
    """Prove every stage accounts for each input exactly once."""
    _require_non_negative(counts)
    if counts.source_dwg != counts.converted_dxf:
        raise CountConservationError(
            f"DWG 与服务器派生 DXF 数量不一致：{counts.source_dwg} != {counts.converted_dxf}"
        )
    if counts.converted_dxf != counts.classification_input:
        raise CountConservationError(
            "服务器派生 DXF 与分类输入数量不一致："
            f"{counts.converted_dxf} != {counts.classification_input}"
        )
    classification_output = counts.classified + counts.review_required + counts.unreadable
    if counts.classification_input != classification_output:
        raise CountConservationError(
            f"分类结果数量不守恒：{counts.classification_input} != {classification_output}"
        )
    # The persisted production contract puts splitter failures inside the
    # manual-review set.  They are a diagnostic subset, not a third output.
    split_output = counts.split_auto_accepted + counts.split_manual_review
    if counts.split_input != split_output:
        raise CountConservationError(f"拆板结果数量不守恒：{counts.split_input} != {split_output}")
    if counts.split_failed > counts.split_manual_review:
        raise CountConservationError(
            f"拆板失败数不能超过人工复核数：{counts.split_failed} > {counts.split_manual_review}"
        )


def inspect_split_archive(payload: bytes) -> SplitArchiveInspection:
    """Validate the two-folder split deliverable without extracting it."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("拆板下载结果不是有效 ZIP") from exc
    members: list[str] = []
    seen: set[str] = set()
    folder_counts = {"原长": 0, "余量增长后短文件": 0}
    with archive:
        for info in archive.infolist():
            raw_name = info.filename.replace("\\", "/")
            path = PurePosixPath(raw_name)
            if not raw_name or raw_name.startswith("/") or path.is_absolute() or ".." in path.parts:
                raise ValueError(f"拆板 ZIP 包含非安全路径：{raw_name}")
            canonical = raw_name.rstrip("/").casefold()
            if not canonical:
                continue
            if canonical in seen:
                raise ValueError(f"拆板 ZIP 包含重复路径：{raw_name}")
            seen.add(canonical)
            if info.is_dir():
                continue
            if info.file_size <= 0:
                raise ValueError(f"拆板 ZIP 包含空文件：{raw_name}")
            if len(path.parts) != 2 or path.parts[0] not in folder_counts:
                raise ValueError(f"拆板 ZIP 包含非交付目录：{raw_name}")
            if path.suffix.casefold() != ".dxf":
                raise ValueError(f"拆板 ZIP 包含非 DXF 文件：{raw_name}")
            folder_counts[path.parts[0]] += 1
            members.append(raw_name)
    if not members:
        raise ValueError("拆板 ZIP 没有 DXF 结果")
    if folder_counts["原长"] != folder_counts["余量增长后短文件"]:
        raise ValueError(
            "拆板 ZIP 两个交付目录数量不一致："
            f"{folder_counts['原长']} != {folder_counts['余量增长后短文件']}"
        )
    return SplitArchiveInspection(
        dxf_count=len(members),
        original_length_count=folder_counts["原长"],
        allowance_extended_count=folder_counts["余量增长后短文件"],
        members=tuple(sorted(members, key=str.casefold)),
    )


class WorkflowRunner:
    """Execute one isolated production project through supported public APIs."""

    _TERMINAL_SUCCESS = frozenset({"completed", "completed_with_review"})
    _TERMINAL_FAILURE = frozenset({"failed", "cancelled", "interrupted", "timed_out"})

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        poll_interval_seconds: float = 1.0,
        stage_timeout_seconds: float = 1800.0,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("轮询间隔不能为负数")
        if stage_timeout_seconds <= 0:
            raise ValueError("阶段超时必须大于零")
        self.client = client
        self.poll_interval_seconds = poll_interval_seconds
        self.stage_timeout_seconds = stage_timeout_seconds
        self._request_ids: list[str] = []

    @staticmethod
    def _error_detail(response: httpx.Response) -> tuple[str, str, str | None]:
        code = f"HTTP_{response.status_code}"
        message = response.reason_phrase or "服务器请求失败"
        request_id = response.headers.get("x-request-id")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return code, message, request_id
        if isinstance(payload, dict):
            meta = payload.get("meta")
            if isinstance(meta, dict) and isinstance(meta.get("request_id"), str):
                request_id = meta["request_id"]
            detail = payload.get("detail", payload)
            if isinstance(detail, dict):
                if isinstance(detail.get("code"), str):
                    code = detail["code"]
                if isinstance(detail.get("message"), str):
                    message = detail["message"]
            elif isinstance(detail, str):
                message = detail
        return code, message, request_id

    async def _request(
        self,
        method: str,
        path: str,
        *,
        stage: str,
        token: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self.client.request(
                method,
                path,
                headers=headers,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise LoadApiError(
                stage=stage,
                status_code=None,
                code="NETWORK_ERROR",
                message=str(exc),
            ) from exc
        request_id = response.headers.get("x-request-id")
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
                candidate = payload["meta"].get("request_id")
                if isinstance(candidate, str):
                    request_id = candidate
        if request_id:
            self._request_ids.append(request_id)
        if response.is_error:
            code, message, error_request_id = self._error_detail(response)
            if error_request_id and error_request_id not in self._request_ids:
                self._request_ids.append(error_request_id)
            raise LoadApiError(
                stage=stage,
                status_code=response.status_code,
                code=code,
                message=message,
                request_id=error_request_id,
            )
        return response

    async def _json(
        self,
        method: str,
        path: str,
        *,
        stage: str,
        token: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = await self._request(
            method,
            path,
            stage=stage,
            token=token,
            **kwargs,
        )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LoadApiError(
                stage=stage,
                status_code=response.status_code,
                code="RESPONSE_JSON_INVALID",
                message="服务器返回了无法解析的数据",
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise LoadApiError(
                stage=stage,
                status_code=response.status_code,
                code="RESPONSE_ENVELOPE_INVALID",
                message="服务器返回结构不符合正式 API 契约",
            )
        return payload["data"]

    async def _poll_batch(
        self,
        workflow_id: int,
        token: str,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.stage_timeout_seconds
        while True:
            data = await self._json(
                "GET",
                f"/api/v1/workflows/{workflow_id}/input-batch",
                stage="等待 DWG 转换",
                token=token,
            )
            counts = data.get("counts", {})
            if data.get("freeze_ready") is True:
                return data
            if isinstance(counts, dict) and int(counts.get("failed", 0)) > 0:
                issues = data.get("issues")
                raise LoadApiError(
                    stage="等待 DWG 转换",
                    status_code=None,
                    code="DWG_CONVERSION_FAILED",
                    message=f"存在转换失败图纸：{issues!r}",
                )
            if time.monotonic() >= deadline:
                raise LoadApiError(
                    stage="等待 DWG 转换",
                    status_code=None,
                    code="STAGE_TIMEOUT",
                    message="DWG 转换超过测试超时",
                )
            await asyncio.sleep(self.poll_interval_seconds)

    async def _poll_stage(
        self,
        workflow_id: int,
        token: str,
        *,
        path: str,
        stage: str,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.stage_timeout_seconds
        while True:
            data = await self._json(
                "GET",
                f"/api/v1/workflows/{workflow_id}/{path}",
                stage=stage,
                token=token,
            )
            status = str(data.get("status") or "")
            if status in self._TERMINAL_SUCCESS:
                return data
            job = data.get("job")
            job_status = str(job.get("status") or "") if isinstance(job, dict) else ""
            if status in self._TERMINAL_FAILURE or job_status in {
                "failed",
                "cancelled",
            }:
                raise LoadApiError(
                    stage=stage,
                    status_code=None,
                    code=f"{stage.upper().replace(' ', '_')}_FAILED",
                    message=f"阶段状态为 {status or job_status}",
                )
            if time.monotonic() >= deadline:
                raise LoadApiError(
                    stage=stage,
                    status_code=None,
                    code="STAGE_TIMEOUT",
                    message=f"{stage}超过测试超时",
                )
            await asyncio.sleep(self.poll_interval_seconds)

    async def run_project(
        self,
        *,
        name: str,
        username: str,
        password: str,
        fixture: LoadFixture,
        project_code: str,
    ) -> ProjectRunResult:
        started = time.monotonic()
        workflow_id: int | None = None
        phase_seconds: dict[str, float] = {}
        job_attempts: dict[str, dict[str, int]] = {}
        counts: ProjectCounts | None = None
        archive_dxf_count = 0
        self._request_ids = []

        def finish_phase(label: str, phase_started: float) -> None:
            phase_seconds[label] = round(time.monotonic() - phase_started, 3)

        try:
            fixture.validate()
            phase_started = time.monotonic()
            session = await self._json(
                "POST",
                "/api/v1/auth/sessions",
                stage="登录",
                json={"username": username, "password": password},
            )
            token = session.get("access_token")
            if not isinstance(token, str) or not token:
                raise LoadApiError(
                    stage="登录",
                    status_code=None,
                    code="LOGIN_TOKEN_MISSING",
                    message="登录成功响应没有访问令牌",
                )
            finish_phase("login", phase_started)

            phase_started = time.monotonic()
            created = await self._json(
                "POST",
                "/api/v1/workflows/production-projects",
                stage="创建生产项目",
                token=token,
                json={
                    "code": project_code,
                    "name": name,
                    "description": "生产环境多账号并发验证",
                },
            )
            workflow = created.get("workflow")
            if not isinstance(workflow, dict) or not isinstance(workflow.get("id"), int):
                raise LoadApiError(
                    stage="创建生产项目",
                    status_code=None,
                    code="WORKFLOW_ID_MISSING",
                    message="建项响应缺少工作流编号",
                )
            workflow_id = workflow["id"]
            await self._json(
                "POST",
                f"/api/v1/workflows/{workflow_id}/input-batch",
                stage="创建输入批次",
                token=token,
            )
            finish_phase("project", phase_started)

            phase_started = time.monotonic()
            excel_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if fixture.excel.suffix.casefold() == ".xlsx"
                else "application/vnd.ms-excel"
            )
            with fixture.excel.open("rb") as excel_stream:
                await self._json(
                    "POST",
                    f"/api/v1/workflows/{workflow_id}/input-excel",
                    stage="上传 Excel",
                    token=token,
                    files={
                        "upload": (
                            fixture.excel.name,
                            excel_stream,
                            excel_type,
                        )
                    },
                )
            with ExitStack() as stack:
                dwg_streams = [stack.enter_context(path.open("rb")) for path in fixture.dwg_files]
                folder_name = fixture.dwg_files[0].parent.name
                files = [
                    ("uploads", (path.name, stream, "application/acad"))
                    for path, stream in zip(
                        fixture.dwg_files,
                        dwg_streams,
                        strict=True,
                    )
                ]
                relative_paths = [f"{folder_name}/{path.name}" for path in fixture.dwg_files]
                await self._json(
                    "POST",
                    f"/api/v1/workflows/{workflow_id}/input-dwg-folder",
                    stage="上传 DWG 文件夹",
                    token=token,
                    files=files,
                    data={"relative_paths": json.dumps(relative_paths, ensure_ascii=False)},
                )
            finish_phase("upload", phase_started)

            phase_started = time.monotonic()
            await self._json(
                "POST",
                (f"/api/v1/workflows/{workflow_id}/input-batch/conversion-requests"),
                stage="提交 DWG 转换",
                token=token,
            )
            batch = await self._poll_batch(workflow_id, token)
            await self._json(
                "POST",
                f"/api/v1/workflows/{workflow_id}/input-batch/freeze",
                stage="冻结生产输入",
                token=token,
            )
            finish_phase("conversion", phase_started)

            phase_started = time.monotonic()
            classification_execution = await self._json(
                "POST",
                (f"/api/v1/workflows/{workflow_id}/stages/dxf_classification/executions"),
                stage="提交 DXF 分类",
                token=token,
                json={"execution_kind": "steel_dxf_classification"},
            )
            classification_job = classification_execution.get("job")
            if isinstance(classification_job, dict):
                job_attempts["dxf_classification"] = {
                    "job_id": int(classification_job.get("id", 0)),
                    "attempt": int(classification_job.get("attempt", 0)),
                }
            classification = await self._poll_stage(
                workflow_id,
                token,
                path="dxf-classification",
                stage="DXF 分类",
            )
            finish_phase("classification", phase_started)

            phase_started = time.monotonic()
            split_execution = await self._json(
                "POST",
                (f"/api/v1/workflows/{workflow_id}/stages/drawing_processing/executions"),
                stage="提交拆板",
                token=token,
                json={"execution_kind": "drawing_processing"},
            )
            split_job = split_execution.get("job")
            if isinstance(split_job, dict):
                job_attempts["drawing_processing"] = {
                    "job_id": int(split_job.get("id", 0)),
                    "attempt": int(split_job.get("attempt", 0)),
                }
            split = await self._poll_stage(
                workflow_id,
                token,
                path="drawing-processing",
                stage="拆板",
            )
            finish_phase("split", phase_started)

            input_counts = batch.get("counts")
            if not isinstance(input_counts, dict):
                input_counts = {}
            counts = ProjectCounts(
                source_dwg=int(input_counts.get("dwg", 0)),
                converted_dxf=int(input_counts.get("paired", 0)),
                classification_input=int(classification.get("input_count", 0)),
                classified=int(classification.get("classified_count", 0)),
                review_required=int(classification.get("review_required_count", 0)),
                unreadable=int(classification.get("unreadable_count", 0)),
                split_input=int(split.get("input_count", 0)),
                split_auto_accepted=int(split.get("auto_accepted_count", 0)),
                split_manual_review=int(split.get("manual_review_count", 0)),
                split_failed=int(split.get("failed_count", 0)),
            )
            validate_project_counts(counts)

            phase_started = time.monotonic()
            archive_response = await self._request(
                "GET",
                (f"/api/v1/workflows/{workflow_id}/stages/drawing_processing/download-archive"),
                stage="下载拆板结果",
                token=token,
            )
            inspection = inspect_split_archive(archive_response.content)
            archive_dxf_count = inspection.dxf_count
            if inspection.original_length_count != counts.split_auto_accepted:
                raise CountConservationError(
                    "拆板正式结果与自动通过数不一致："
                    f"{inspection.original_length_count} != "
                    f"{counts.split_auto_accepted}"
                )
            finish_phase("download", phase_started)
            return ProjectRunResult(
                name=name,
                succeeded=True,
                workflow_id=workflow_id,
                counts=counts,
                archive_dxf_count=archive_dxf_count,
                job_attempts=job_attempts,
                request_ids=tuple(dict.fromkeys(self._request_ids)),
                elapsed_seconds=round(time.monotonic() - started, 3),
                phase_seconds=phase_seconds,
            )
        except Exception as exc:
            if isinstance(exc, LoadApiError):
                error_code = exc.code
                error_message = exc.message
                error_stage = exc.stage
                http_status = exc.status_code
            else:
                error_code = type(exc).__name__
                error_message = str(exc)
                error_stage = "本地核验"
                http_status = None
            return ProjectRunResult(
                name=name,
                succeeded=False,
                workflow_id=workflow_id,
                counts=counts,
                archive_dxf_count=archive_dxf_count,
                job_attempts=job_attempts,
                request_ids=tuple(dict.fromkeys(self._request_ids)),
                elapsed_seconds=round(time.monotonic() - started, 3),
                phase_seconds=phase_seconds,
                error_code=error_code,
                error_message=error_message,
                error_stage=error_stage,
                http_status=http_status,
            )


def report_exit_code(results: Sequence[ScenarioResult]) -> int:
    return 0 if results and all(result.succeeded for result in results) else 1


def select_dwg_files(directory: Path, *, limit: int) -> tuple[Path, ...]:
    if limit <= 0:
        raise ValueError("DWG 数量必须是正整数")
    if not directory.is_dir():
        raise ValueError(f"DWG 测试文件夹不存在：{directory}")
    available = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.casefold() == ".dwg"
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )
    if len(available) < limit:
        raise ValueError(f"DWG 测试文件夹只有 {len(available)} 张图纸，少于要求的 {limit} 张")
    return tuple(available[:limit])


def summarize_project_results(
    name: str,
    results: Sequence[ProjectRunResult],
) -> dict[str, Any]:
    if not results:
        raise ValueError("并发场景至少需要一个项目结果")
    phase_names = sorted({phase for result in results for phase in result.phase_seconds})
    phase_summary: dict[str, dict[str, float]] = {}
    for phase in phase_names:
        samples = [
            result.phase_seconds[phase] for result in results if phase in result.phase_seconds
        ]
        phase_summary[phase] = {
            "p50": round(percentile(samples, 50), 3),
            "p95": round(percentile(samples, 95), 3),
        }
    elapsed = [result.elapsed_seconds for result in results]
    error_codes = Counter(
        result.error_code for result in results if not result.succeeded and result.error_code
    )
    succeeded_count = sum(result.succeeded for result in results)
    return {
        "name": name,
        "project_count": len(results),
        "succeeded_count": succeeded_count,
        "failed_count": len(results) - succeeded_count,
        "success_rate": round(succeeded_count / len(results), 4),
        "elapsed_seconds": {
            "p50": round(percentile(elapsed, 50), 3),
            "p95": round(percentile(elapsed, 95), 3),
        },
        "phase_seconds": phase_summary,
        "error_codes": dict(sorted(error_codes.items())),
        "projects": [result.to_report() for result in results],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过正式 HTTP API 执行多账号生产流程压力测试。",
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--accounts",
        required=True,
        help="逗号分隔的账号；凭据只从 DWG_LOAD_CREDENTIALS_JSON 读取",
    )
    parser.add_argument("--dwg-dir", type=Path, required=True)
    parser.add_argument("--excel", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--concurrency", type=parse_positive_int_list, default=[1])
    parser.add_argument(
        "--dwg-count",
        type=int,
        default=30,
        help="每个项目精确上传的 DWG 数量，默认 30",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="阶段状态轮询间隔秒数",
    )
    parser.add_argument(
        "--stage-timeout",
        type=float,
        default=3600,
        help="每个异步阶段超时秒数",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=1800,
        help="单次 HTTP 上传或下载超时秒数",
    )
    parser.add_argument(
        "--login-spacing",
        type=float,
        default=1.0,
        help="同一档内各项目登录错峰秒数",
    )
    parser.add_argument(
        "--release-label",
        default="",
        help="写入报告的服务器发布标识",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="仅测试自签名 HTTPS 时关闭证书校验",
    )
    return parser


def _load_credentials(account_names: Sequence[str]) -> dict[str, str]:
    raw = os.environ.get("DWG_LOAD_CREDENTIALS_JSON", "")
    if not raw:
        raise ValueError("缺少账号凭据环境变量")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("账号凭据环境变量不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("账号凭据环境变量必须是对象")
    credentials: dict[str, str] = {}
    for account in account_names:
        secret = payload.get(account)
        if not isinstance(secret, str) or not secret:
            raise ValueError(f"账号 {account} 缺少凭据")
        credentials[account] = secret
    return credentials


async def _run_matrix(
    *,
    base_url: str,
    fixture: LoadFixture,
    accounts: Sequence[str],
    credentials: Mapping[str, str],
    concurrency_levels: Sequence[int],
    poll_interval: float,
    stage_timeout: float,
    request_timeout: float,
    login_spacing: float,
    verify_tls: bool,
    prefix: str,
) -> list[dict[str, Any]]:
    maximum = max(concurrency_levels)
    timeout = httpx.Timeout(request_timeout, connect=min(request_timeout, 60))
    limits = httpx.Limits(
        max_connections=max(8, maximum * 2),
        max_keepalive_connections=max(4, maximum),
    )
    scenarios: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        limits=limits,
        verify=verify_tls,
        follow_redirects=False,
    ) as client:
        project_serial = 0
        for level in concurrency_levels:
            scenario_name = f"concurrency-{level}"

            async def run_one(
                index: int,
                scenario_level: int = level,
            ) -> ProjectRunResult:
                nonlocal project_serial
                await asyncio.sleep(index * login_spacing)
                project_serial += 1
                serial = project_serial
                account = accounts[index % len(accounts)]
                code = f"{prefix}-C{scenario_level}-P{serial}"
                runner = WorkflowRunner(
                    client=client,
                    poll_interval_seconds=poll_interval,
                    stage_timeout_seconds=stage_timeout,
                )
                return await runner.run_project(
                    name=f"{prefix} 并发{scenario_level} 项目{serial}",
                    username=account,
                    password=credentials[account],
                    fixture=fixture,
                    project_code=code,
                )

            started = time.monotonic()
            results = await asyncio.gather(*(run_one(index) for index in range(level)))
            summary = summarize_project_results(scenario_name, results)
            summary["wall_seconds"] = round(time.monotonic() - started, 3)
            scenarios.append(summary)
    return scenarios


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    accounts = [item.strip() for item in args.accounts.split(",") if item.strip()]
    if not accounts:
        parser.error("至少需要一个测试账号")
    try:
        credentials = _load_credentials(accounts)
        if args.poll_interval < 0:
            raise ValueError("轮询间隔不能为负数")
        if args.stage_timeout <= 0 or args.request_timeout <= 0:
            raise ValueError("超时时间必须大于零")
        if args.login_spacing < 0:
            raise ValueError("登录错峰不能为负数")
        dwg_files = select_dwg_files(args.dwg_dir, limit=args.dwg_count)
        fixture = LoadFixture(excel=args.excel, dwg_files=dwg_files)
        fixture.validate()
    except ValueError as exc:
        parser.error(str(exc))
    started_at = datetime.now(UTC)
    prefix = f"LOAD-{started_at.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2).upper()}"
    try:
        scenarios = asyncio.run(
            _run_matrix(
                base_url=args.base_url,
                fixture=fixture,
                accounts=accounts,
                credentials=credentials,
                concurrency_levels=args.concurrency,
                poll_interval=args.poll_interval,
                stage_timeout=args.stage_timeout,
                request_timeout=args.request_timeout,
                login_spacing=args.login_spacing,
                verify_tls=not args.insecure,
                prefix=prefix,
            )
        )
    except KeyboardInterrupt:
        print("压力测试被人工中止", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"压力测试驱动器异常：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    report = {
        "schema": "production-workflow-load-report/v1",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "release_label": args.release_label,
        "base_url": args.base_url,
        "prefix": prefix,
        "accounts": accounts,
        "concurrency_levels": args.concurrency,
        "fixture": {
            "excel": {
                "name": fixture.excel.name,
                "size_bytes": fixture.excel.stat().st_size,
                "sha256": _sha256(fixture.excel),
            },
            "dwg_count": len(fixture.dwg_files),
            "dwgs": [
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for path in fixture.dwg_files
            ],
        },
        "scenarios": scenarios,
    }
    safe_report = redact_secrets(report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(args.report.suffix + ".tmp")
    temporary.write_text(
        json.dumps(safe_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.report)
    scenario_results = [
        ScenarioResult(
            name=str(scenario["name"]),
            succeeded=int(scenario["failed_count"]) == 0,
            error_code=(None if int(scenario["failed_count"]) == 0 else "PROJECT_FAILURE"),
        )
        for scenario in scenarios
    ]
    exit_code = report_exit_code(scenario_results)
    succeeded = sum(int(item["succeeded_count"]) for item in scenarios)
    total = sum(int(item["project_count"]) for item in scenarios)
    print(f"压力测试完成：{succeeded}/{total} 个项目成功；报告：{args.report}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
