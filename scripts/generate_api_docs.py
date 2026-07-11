#!/usr/bin/env python3
"""Generate synchronized English and Chinese API route references from FastAPI."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app  # noqa: E402

GROUP_NAMES = {
    "health": ("Health", "健康检查"),
    "auth": ("Authentication", "认证"),
    "users": ("Users", "用户"),
    "roles": ("Roles and permissions", "角色与权限"),
    "permissions": ("Roles and permissions", "角色与权限"),
    "projects": ("Projects", "项目"),
    "files": ("Files and downloads", "文件与下载"),
    "drawings": ("Drawings", "图纸"),
    "jobs": ("Jobs", "任务"),
    "results": ("Results and reviews", "结果与复核"),
    "reviews": ("Results and reviews", "结果与复核"),
    "audit-logs": ("Audit", "审计"),
    "agent-runs": ("Agent", "Agent"),
    "agent-tools": ("Agent", "Agent"),
    "system": ("System", "系统"),
    "excel-final": ("Excel Final", "Excel Final"),
}


def group_key(path: str) -> str:
    if path.startswith("/health"):
        return "health"
    parts = path.split("/")
    return parts[3] if len(parts) > 3 else "other"


def route_rows() -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for path, operations in app.openapi()["paths"].items():
        methods = ", ".join(method.upper() for method in operations)
        grouped[group_key(path)].append((methods, path))
    return grouped


def render(*, chinese: bool) -> str:
    title = "API 参考" if chinese else "API Reference"
    intro = (
        "本文件由 `cd backend && uv run python ../scripts/generate_api_docs.py` 从 FastAPI OpenAPI schema 生成。"
        "端点变更必须先修改代码和测试，再重新生成中英文参考。"
        if chinese
        else "This file is generated from the FastAPI OpenAPI schema by "
        "`cd backend && uv run python ../scripts/generate_api_docs.py`. Change code and tests first, then regenerate both languages."
    )
    common = (
        """
## 统一约定

- 本地直连基地址：`http://127.0.0.1:8010`；Nginx 入口：`http://127.0.0.1:8080`；容器内部 API 端口：`8000`。
- 除 `/health`、`/health/ready`、`POST /api/v1/auth/sessions` 和刷新端点外，业务端点均要求 Bearer access token。
- 成功响应使用 `{data, meta}`；分页响应额外包含 `pagination`，`total` 来自 SQL `COUNT(*)`。
- 错误响应使用 `{error: {code, message, details}, meta}`，不会向客户端暴露 traceback、DSN 或本机路径。
- `GET /api/v1/jobs/{job_id}/events` 使用 SSE cookie 认证并轮询 MySQL 权威状态；URL 中不传 token。
- 下载流程为：鉴权获取短期签名 URL，再携带 Bearer token 下载。403、408、429、5xx 或网络错误重试时必须重新获取签名。
- 任务重试递增 `attempt`；步骤查询可用 `?attempt=N`，旧 worker 不能覆盖新 attempt。
- SSE snapshot 只包含当前 attempt 的 steps；无项目 Job 的结果仅管理员或创建者可访问。
- `AGENT_ENABLED=false` 时 Agent 端点返回 503；启用后详情和步骤受创建者、管理员或项目成员边界约束。
"""
        if chinese
        else """
## Conventions

- Local API: `http://127.0.0.1:8010`; Nginx: `http://127.0.0.1:8080`; container API port: `8000`.
- Business routes require a Bearer access token except `/health`, `/health/ready`, `POST /api/v1/auth/sessions`, and refresh.
- Success uses `{data, meta}`. Paginated responses add `pagination`; `total` is an exact SQL `COUNT(*)`.
- Errors use `{error: {code, message, details}, meta}` and never expose tracebacks, DSNs, or host paths.
- `GET /api/v1/jobs/{job_id}/events` uses an SSE cookie and polls authoritative MySQL state; no token is placed in the URL.
- Downloads first obtain a short-lived signed URL, then download with Bearer auth. A retry after network, 403, 408, 429, or 5xx obtains a fresh signature.
- Job retries increment `attempt`; steps accept `?attempt=N`, and stale workers cannot overwrite a newer attempt.
- SSE snapshots contain only the current attempt's steps. Results of an unscoped job are restricted to administrators or its creator.
- With `AGENT_ENABLED=false`, Agent routes return 503. When enabled, run details and steps are scoped to the creator, administrators, or project members.
"""
    )
    sections = [f"# {title}", "", intro, common]
    grouped = route_rows()
    seen: set[str] = set()
    for key in GROUP_NAMES:
        display = GROUP_NAMES[key][1 if chinese else 0]
        if display in seen or key not in grouped:
            continue
        seen.add(display)
        rows: list[tuple[str, str]] = []
        for candidate, names in GROUP_NAMES.items():
            if names[1 if chinese else 0] == display:
                rows.extend(grouped.get(candidate, []))
        sections.extend([f"## {display}", "", "| Method | Path |", "|---|---|"])
        sections.extend(f"| `{method}` | `{path}` |" for method, path in rows)
        sections.append("")
    sections.extend(
        [
            "## 运行时文档" if chinese else "## Runtime documentation",
            "",
            "启动后访问 `/docs`、`/redoc` 或 `/openapi.json` 获取请求/响应 schema。"
            if chinese
            else "Use `/docs`, `/redoc`, or `/openapi.json` for request and response schemas while the API is running.",
            "",
        ]
    )
    return "\n".join(sections)


def main() -> None:
    (ROOT / "docs/api.md").write_text(render(chinese=False), encoding="utf-8")
    (ROOT / "docs/zh/api.md").write_text(render(chinese=True), encoding="utf-8")


if __name__ == "__main__":
    main()
