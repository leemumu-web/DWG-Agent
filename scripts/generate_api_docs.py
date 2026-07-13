#!/usr/bin/env python3
"""Generate the Chinese API route reference from FastAPI."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app  # noqa: E402

GROUP_NAMES = {
    "health": "健康检查",
    "auth": "认证",
    "data-admin": "数据控制台",
    "users": "用户",
    "roles": "角色与权限",
    "permissions": "角色与权限",
    "projects": "项目",
    "files": "文件与下载",
    "drawings": "图纸",
    "jobs": "任务",
    "results": "结果与复核",
    "reviews": "结果与复核",
    "audit-logs": "审计",
    "agent-runs": "Agent（禁用边界）",
    "agent-tools": "Agent（禁用边界）",
    "system": "系统",
    "excel-final": "Excel Final",
    "workflows": "生产流程",
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


def render() -> str:
    intro = (
        "本文件由 `cd backend && uv run python ../scripts/generate_api_docs.py` 从 FastAPI "
        "OpenAPI schema 生成。端点变更必须先修改代码和测试，再重新生成本文件。"
        "路由表只证明接口存在；功能开关、权限、外部依赖和真实样本仍可能阻止业务执行。"
    )
    common = """
## 统一约定

- 本地直连基地址：`http://127.0.0.1:8010`；Nginx 入口：`http://127.0.0.1:8080`；容器内部 API 端口同为 `8010`。
- 除 `/health`、`/health/ready`、`POST /api/v1/auth/sessions` 和刷新端点外，业务端点均要求 Bearer access token。
- 成功响应使用 `{data, meta}`；分页响应额外包含 `pagination`，`total` 来自 SQL `COUNT(*)`。
- 错误响应使用 `{error: {code, message, details}, meta}`，不会向客户端暴露 traceback、DSN 或本机路径。
- `GET /api/v1/jobs/{job_id}/events` 使用 SSE cookie 认证并轮询 MySQL 权威状态；URL 中不传 token。
- 下载流程为：鉴权获取短期签名 URL，再携带 Bearer token 下载。403、408、429、5xx 或网络错误重试时必须重新获取签名。
- 任务重试递增 `attempt`；步骤查询可用 `?attempt=N`，旧 worker 不能覆盖新 attempt。
- SSE snapshot 只包含当前 attempt 的 steps；无项目 Job 的结果仅管理员或创建者可访问。
- 数据控制台读取允许 `admin/auditor`，扫描与处置执行只允许 `admin`；处置必须先预检，再携带绑定操作人和目标摘要的短期 token 与幂等键执行。
- 文件/流水/finding 使用服务端页码分页；对象清单使用不透明 cursor。永久清理未登记对象还必须提交确认词 `PURGE`。
- `AGENT_ENABLED=false` 时 Agent 端点返回 503；仓库没有可执行 Agent task，本项目也不把 Agent 执行列为当前交付目标。
"""
    excel_final_contract = """
## Excel Final 幂等与监视契约

`POST /process` 和 `POST /upload-and-process` 接受 `Idempotency-Key`。键去除首尾空白后必须为 1-96 个 ASCII 字母、数字、点、下划线、冒号或连字符。服务端按端点作用域保存到 `jobs.request_key`，数据库唯一约束覆盖 `(created_by, task_type, request_key)`：首次提交返回 `reused=false` 并分发 Job；相同键和相同参数重放返回同一 `job_id`、`reused=true`，不再次分发；同一 process 键改用另一个 `file_id` 返回 `409 IDEMPOTENCY_KEY_REUSED`。不带键保留旧的每次创建行为，仓库前端始终发送键。

`upload-and-process` 同时把键用于 inbound 上传流水。已完成请求的 HTTP 响应丢失后，重放复用同一 StoredFile、对象和 Job；第一个上传仍在进行时返回 `409 TRANSFER_IN_PROGRESS`，失败终态不能被假装为成功重放。DXF→Excel 桥使用 `dxf2excel-{extraction_job_id}-{result_file_id}`，同一提取结果跨刷新/多标签不会重复登记；正常失败重试仍调用既有 retry endpoint 并递增 attempt。

`GET /health` 除 Stage/依赖/五金手册字段外，还返回 `database_backend`、`database_available`、`storage_backend`、`storage_available`、`storage_bucket` 与稳定的 `degraded_components`。`ready` 要求处理开关、Stage/依赖、手册库、业务数据库和对象存储同时可用；响应不包含底层连接异常或凭据。

前端 `/files/excel-final` 支持 `job_id`、`batch_page`、`batch_size`、`batch_id`、`part_no`、`spec`、`material`、`search_page`、`search_size` 和内部搜索激活标记。默认值不强制写入 URL；关闭抽屉、清空搜索及分页更新只修改自身参数，不覆盖同页任务状态。
"""
    sections = ["# API 参考", "", intro, common]
    grouped = route_rows()
    seen: set[str] = set()
    for key in GROUP_NAMES:
        display = GROUP_NAMES[key]
        if display in seen or key not in grouped:
            continue
        seen.add(display)
        rows: list[tuple[str, str]] = []
        for candidate, name in GROUP_NAMES.items():
            if name == display:
                rows.extend(grouped.get(candidate, []))
        sections.extend([f"## {display}", "", "| Method | Path |", "|---|---|"])
        sections.extend(f"| `{method}` | `{path}` |" for method, path in rows)
        sections.append("")
    sections.extend(
        [
            excel_final_contract,
            "## 运行时文档",
            "",
            "development/debug 模式启动后，访问 `/docs`、`/redoc` 或 `/openapi.json` 获取请求/响应 schema。",
            "当 `APP_ENV=production` 且 `DEBUG=false` 时，这三个运行时文档入口有意关闭；生产应使用本生成文件和版本化 OpenAPI artifact。",
            "",
        ]
    )
    return "\n".join(sections)


def main() -> None:
    (ROOT / "docs/api.md").write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
