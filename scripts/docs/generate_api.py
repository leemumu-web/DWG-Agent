#!/usr/bin/env python3
"""Generate the Chinese API route reference from FastAPI."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_DOC_PATH = ROOT / "docs" / "reference" / "api.md"
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
    "control-plane": "运行与通信控制平面",
    "excel-final": "Excel Final",
    "workflows": "生产流程",
    "remnant-materials": "余料材质",
    "remnant-import-batches": "余料导入",
    "remnant-import-items": "余料导入",
    "remnants": "余料库存",
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
    schema = app.openapi()
    path_count = len(schema["paths"])
    operation_count = sum(len(operations) for operations in schema["paths"].values())
    intro = (
        "本文件由 `cd backend && uv run python ../scripts/docs/generate_api.py` 从 FastAPI "
        "OpenAPI schema 生成。端点变更必须先修改代码和测试，再重新生成本文件。"
        f"当前 OpenAPI 包含 **{path_count} 个 path、{operation_count} 个 operation**。"
        "路由表只证明接口存在；功能开关、权限、外部依赖和真实样本仍可能阻止业务执行。"
    )
    common = """
## 统一约定

- 本地直连基地址：`http://127.0.0.1:8010`；Nginx 入口：`http://127.0.0.1:8080`；容器内部 API 端口同为 `8010`。
- 除健康检查、登录/刷新和工作流导出的单次 download URL 外，业务端点均要求 Bearer access token。分批导出与图纸分类选择导出的 download URL 都使用创建接口写入的路径级 HttpOnly 能力 cookie，不接受 query token。
- 成功响应使用 `{data, meta}`；分页响应额外包含 `pagination`，`total` 来自 SQL `COUNT(*)`。
- 错误响应使用 `{error: {code, message, details}, meta}`，不会向客户端暴露 traceback、DSN 或本机路径。
- 仓库前端优先展示 `error.message`；422 会展开 `details.errors` 的字段路径和原因，并附带 `error.code` 与 `meta.request_id`。客户端不得只显示“HTTP 4xx”而隐藏服务端原因。无法连接、超时以及无结构化响应时才使用状态码兜底文案。
- `GET /api/v1/workflows/jobs/{job_id}/events` 与聚合 `GET /api/v1/workflows/jobs/events/stream` 使用 SSE cookie 认证并轮询 MySQL 权威状态；URL 中不传 token。聚合流每次最多观察 200 个文件并在全部终态后关闭。
- 通用下载流程为：鉴权获取短期签名 URL，再携带 Bearer token 下载。工作流分批导出与图纸分类选择导出是例外：创建接口签发路径级 HttpOnly cookie，浏览器原生 `<a>` 直接下载流式 ZIP。403、408、429、5xx 或网络错误重试时必须重新获取相应能力或签名。
- 任务重试递增 `attempt`；步骤查询可用 `?attempt=N`，旧 worker 不能覆盖新 attempt。
- 双向 CAD 批量创建一次接受最多 200 个文件并保留每文件 Job；批量取消只作用于请求内且有权写入的 Job，不等同于管理员全局取消。
- SSE snapshot 只包含当前 attempt 的 steps；无项目 Job 的结果仅管理员或创建者可访问。
- 数据控制台读取允许 `admin/auditor`，扫描与处置执行只允许 `admin`；处置必须先预检，再携带绑定操作人和目标摘要的短期 token 与幂等键执行。
- 文件/流水/finding 使用服务端页码分页；对象清单使用不透明 cursor。永久清理未登记对象还必须提交确认词 `PURGE`。
- `AGENT_ENABLED=false` 时 Agent 端点返回 503；仓库没有可执行 Agent task，本项目也不把 Agent 执行列为当前交付目标。
"""
    cad_conversion_contract = """
## CAD 转换生产契约

### 批量创建转换任务

`POST /api/v1/workflows/jobs/batches` 仅接受 `convert_dwg_to_dxf` 或 `convert_dxf_to_dwg`，每次请求包含 1-200 个 `file_id`。服务端对重复 ID 去重，先验证全部文件存在性、读权限以及源扩展名（DWG→DXF 只接受 `.dwg`，DXF→DWG 只接受 `.dxf`），再为每个文件创建独立 Job。任一文件不存在、不可读或类型错误时，该 HTTP 请求不留下部分 Job。

```json
{
  "task_type": "convert_dwg_to_dxf",
  "file_ids": [101, 102, 103],
  "precision_level": "normal"
}
```

HTTP 202 返回标准 envelope：

```json
{
  "data": {
    "jobs": [
      {
        "id": 9001,
        "task_type": "convert_dwg_to_dxf",
        "status": "queued",
        "attempt": 1,
        "progress": 0,
        "params_json": {"file_id": 101}
      }
    ]
  },
  "meta": {"request_id": "req_...", "timestamp": "..."}
}
```

仓库前端会对超过 200 个的提交去重、分块，每轮最多并发 3 个请求，并分别保留 `submittedFileIds`、`unsubmittedFileIds` 和 `errors`。这是客户端的“部分块成功”处理，不表示单个 `/jobs/batches` 请求会部分提交。失败块不自动重试，操作员通过“提交/重试”补交，避免响应丢失时无边界重复创建。

| HTTP | `error.code` | 含义 |
|---|---|---|
| 401 | `INVALID_TOKEN` / `TOKEN_REVOKED` / `USER_NOT_ACTIVE` | access 会话无效。 |
| 403 | `FORBIDDEN` | 至少一个文件不可读；该请求未创建 Job。 |
| 404 | `NOT_FOUND` | 至少一个文件不存在或已删除。 |
| 422 | `VALIDATION_ERROR` | task type、ID 数量或 `precision_level` 不符合 schema。 |
| 422 | `INVALID_CONVERSION_SOURCE` | 至少一个文件扩展名与转换方向不匹配。 |
| 503 | `DXF_PIPELINE_DISABLED` / `DXF2DWG_PIPELINE_DISABLED` | 对应管线开关未启用。 |

该端点同样没有 `Idempotency-Key`；只保证一个请求体内重复 `file_id` 去重。完整重放已成功的 HTTP 请求会为同一文件创建新 Job；响应丢失后应先查询最新 Job，不应无条件重放。

### 多文件夹原子软删除

`POST /api/v1/files/batches/bulk-delete` 完整删除 1-100 个批次。`batch_names` 中每个名称会去除首尾空白，不能为空，最长 128 个字符；重复名称只处理一次。

```json
{
  "batch_names": ["BH_拆板前_dwg", "BH_拆板后_dwg"]
}
```

HTTP 200 响应：

```json
{
  "data": {
    "deleted_batch_count": 2,
    "deleted_file_count": 84,
    "cancelled_job_count": 3
  },
  "meta": {"request_id": "req_...", "timestamp": "..."}
}
```

`deleted_file_count` 统计被软删除的 `files` 记录，包含具有相同 `batch_name` 的源文件和已登记生成结果，因此可大于界面上的源文件数。软删除会写入文件状态、删除时间、流转账本和审计记录；它不在请求内立即物理回收对象字节。

服务端在任何写入前确认所有文件夹存在，并对其中每个文件执行“上传者或管理员”删除授权。同一事务内会取消关联的 `pending`、`queued`、`running`、`validating` 和 `waiting_cad_worker` 双向 CAD Job。任一文件夹缺失、任一文件无权或写入失败时，所有文件夹和 Job 一起回滚。

| HTTP | `error.code` | 含义与客户端处理 |
|---|---|---|
| 401 | `INVALID_TOKEN` / `TOKEN_REVOKED` / `USER_NOT_ACTIVE` | 未登录或 access 会话无效；刷新会话后重新确认操作。 |
| 403 | `FORBIDDEN` | 至少一个文件无删除权限；整批未变更。 |
| 404 | `NOT_FOUND` | 至少一个文件夹不存在或已全部删除；整批未变更。 |
| 422 | `VALIDATION_ERROR` | 数组为空、超过 100 项、包含空名或超长名称。 |
| 500 | `INTERNAL_ERROR` | 事务回滚；保留当前选择并刷新权威状态后再决定是否重试。 |

该端点没有 `Idempotency-Key` 契约。如果首次请求已提交但响应丢失，直接重放可因批次已删除而返回 404；客户端应先刷新文件夹列表，不能把 404 当作“从未执行”的证据。

### ZIP 打包可用性预检与严格下载

`POST /api/v1/files/download-zip/preview` 与正式 `POST /api/v1/files/download-zip` 接受相同请求体。预检按 `file_id` 去重，检查源文件和已登记转换结果是否能覆盖每个请求格式，但不读取全部对象字节。

```json
{
  "file_ids": [101, 102],
  "formats": ["dwg", "dxf"],
  "folder_name": "图纸导出"
}
```

HTTP 200 预检响应：

```json
{
  "data": {
    "file_count": 2,
    "formats": [
      {
        "format": "dwg",
        "available_count": 2,
        "missing_count": 0,
        "missing_file_ids": [],
        "complete": true
      },
      {
        "format": "dxf",
        "available_count": 1,
        "missing_count": 1,
        "missing_file_ids": [102],
        "complete": false
      }
    ],
    "can_download": false
  },
  "meta": {"request_id": "req_...", "timestamp": "..."}
}
```

`missing_file_ids` 最多返回 20 个样本，`missing_count` 始终是准确总数。前端只允许选择 `complete=true` 的格式，并默认选择当前转换页的源格式。预检不是锁：预检和正式下载之间若结果被删除或对象丢失，正式下载仍以严格校验为准。

| HTTP | `error.code` | 含义与客户端处理 |
|---|---|---|
| 401 | `INVALID_TOKEN` / `TOKEN_REVOKED` / `USER_NOT_ACTIVE` | 会话无效；刷新登录状态后重新预检。 |
| 403 | `FORBIDDEN` | 至少一个源文件不可读。 |
| 404 | `NOT_FOUND` / `FILE_EXPORT_SOURCE_MISSING` | 至少一个源文件不存在或已删除。 |
| 409 | `FILE_EXPORT_FORMAT_UNAVAILABLE` | 正式下载时至少一个格式不完整；保留弹窗并重新预检。 |
| 409 | `STORAGE_INCONSISTENT` | 元数据存在但所需对象字节不可读；停止重放并排查存储一致性。 |
| 422 | `INVALID_PARAMS` / `VALIDATION_ERROR` | ID 或格式为空，或格式不是 `dwg`/`dxf`。 |

正式 ZIP 不静默跳过缺失文件或格式。只有全部请求项可读取时才返回下载流，因此成功 ZIP 可以作为“请求集合完整”的证据，但不能证明未请求格式存在。

### 进度和可补交口径

Job 的 `progress` 是单任务快照。转换页的“成功进度”按当前范围文件数作分母：`succeeded` 计 100，非停滞 active Job 计经 0-100 截断的当前进度，`failed`、`cancelled`、无 Job 和超过 60 秒仍为 0% 的停滞 `queued` 计 0。因此失败 Job 保留的历史进度不会抬高汇总。

只有最新 Job 状态加载完成后才显示“未提交”。无 Job、`failed`、`cancelled` 或停滞 `queued` 文件进入“提交/重试”集合；活动任务与可补交文件可同时存在，暂停和补交入口因而可同时显示。
"""
    excel_final_contract = """
## Excel Final 幂等与监视契约

`POST /process` 和 `POST /upload-and-process` 接受 `Idempotency-Key`。键去除首尾空白后必须为 1-96 个 ASCII 字母、数字、点、下划线、冒号或连字符。服务端按端点作用域保存到 `jobs.request_key`，数据库唯一约束覆盖 `(created_by, task_type, request_key)`：首次提交返回 `reused=false` 并分发 Job；相同键和相同参数重放返回同一 `job_id`、`reused=true`，不再次分发；同一 process 键改用另一个 `file_id` 返回 `409 IDEMPOTENCY_KEY_REUSED`。不带键保留旧的每次创建行为，仓库前端始终发送键。

`upload-and-process` 同时把键用于 inbound 上传流水。已完成请求的 HTTP 响应丢失后，重放复用同一 StoredFile、对象和 Job；第一个上传仍在进行时返回 `409 TRANSFER_IN_PROGRESS`，失败终态不能被假装为成功重放。DXF→Excel 桥使用 `dxf2excel-{extraction_job_id}-{result_file_id}`，同一提取结果跨刷新/多标签不会重复登记；正常失败重试仍调用既有 retry endpoint 并递增 attempt。

`GET /health` 除 Stage/依赖/五金手册字段外，还返回 `database_backend`、`database_available`、`storage_backend`、`storage_available`、`storage_bucket` 与稳定的 `degraded_components`。`ready` 要求处理开关、Stage/依赖、手册库、业务数据库和对象存储同时可用；响应不包含底层连接异常或凭据。

前端 `/files/excel-final` 支持 `job_id`、`batch_page`、`batch_size`、`batch_id`、`part_no`、`spec`、`material`、`search_page`、`search_size` 和内部搜索激活标记。默认值不强制写入 URL；关闭抽屉、清空搜索及分页更新只修改自身参数，不覆盖同页任务状态。
"""
    workflow_contract = """
## Linux 生产工作流契约

`GET /api/v1/workflows/templates` 返回后端权威模板和阶段能力。新建 `linux_production` 使用 definition_revision 4，固定为 `source_intake`、`dxf_classification`、`drawing_processing`、`excel_stage1`、`excel_stage2`、`design_barrier`、`cam_packaging`、`windows_cam`、`result_acceptance`、`delivery_archive` 十阶段；历史流程不自动改写。`excel_stage2` 已实现，合同为 `stage1_excel + classified_dxf（当前分类账中冻结的拆板前 BH DXF）→ bh_setback_excel + stage2_excel`。先调用 `GET .../stages/excel_stage2/preflight` 核对第一阶段正式 attempt、分类正式 attempt、对象摘要和 BH 清单；通过后以 `execution_kind=excel_stage2` 创建专用队列 Job。读取表和正式深化表分别经 `download-reader-result`、`download-result` 返回一个 `.xlsx`，不混入 ZIP。服务未启用时稳定返回 `EXCEL_STAGE2_PIPELINE_DISABLED`。每个阶段声明执行方式、实现状态、execution kind、所需输入和产物类型；前端不得自行把 placeholder 判断为已实现。

`POST /api/v1/workflows/{workflow_id}/artifacts` 只绑定现有 `file_id` / `result_id`，不接收文件字节。服务端同时验证项目写权限与目标资源读权限；相同 workflow、stage、artifact type、file、result 的重放返回原 artifact 和 `reused=true`。

`source_intake` 不再允许用通用 artifact/completion 绕过。人工通过 `POST /api/v1/workflows/{workflow_id}/input-excel` 单独上传一个 `.xls`/`.xlsx`，并通过 `POST .../input-dwg-folder` 上传一个只含 DWG 的文件夹；浏览器发现其他文件时确认后仅发送 DWG，服务端仍在存储前审核统一根目录、安全相对路径、扩展名和重名。人工 `.dxf` 不会进入请求，绕过前端发送到 DWG 入口会返回 `INPUT_DWG_FOLDER_FILE_TYPE_NOT_ALLOWED`；服务器通过 `POST .../input-batch/conversion-requests` 复用现有 `convert_dwg_to_dxf` Job，失败重试递增 attempt，成功 Result 必须产生同名、可读且格式有效的 DXF。生产产物统一通过 `GET /api/v1/workflows/{workflow_id}/download-archive` 下载按阶段组织的 ZIP，不提供逐文件下载入口。

`GET .../input-batch` 返回每个 DWG 的上传、Job、attempt、进度、派生 DXF、配对和错误建议。批次创建以 savepoint 处理唯一键竞态；文件登记/移除/转换以批次行锁串行化，避免并发产生两个 Excel。明确的 broker 投递失败以 status + attempt 条件把仍 queued 的 Job 标为 `JOB_ENQUEUE_FAILED`，重放可进入 retry；已领取 Job 不被覆盖。

`POST .../input-batch/freeze` 在行锁内重新读取所有源对象，核对大小、SHA-256、真实格式、唯一 Excel 和规范化文件名，然后为每个 DWG 创建指向规范 DXF 的 Drawing/Version、挂接 `source_dwg`/`canonical_dxf`/`source_excel` artifact、计算规范 JSON 清单 SHA-256，并原子完成 `source_intake`。冻结后所有增删和转换请求被拒绝；通用 `/files` 删除同样拒绝冻结清单中的 DWG、Excel 和派生 DXF，返回 `409 FILE_REFERENCED_BY_FROZEN_INPUT`。模板同时公开 `required_outputs`，阶段只有在全部必需产物存在时才能成功。

`linux_production` 对每阶段强制执行模板声明的 artifact type 白名单；不匹配返回 `422 WORKFLOW_ARTIFACT_TYPE_INVALID`。因此 placeholder/external 阶段必须提交约定类型的真实交接产物，不能用任意文件满足 completion。旧模板未声明白名单，保持兼容。

`POST /api/v1/workflows/{workflow_id}/stages/{stage_code}/executions` 只执行当前阶段。`dxf_classification` 接收 `execution_kind=steel_dxf_classification` 并从冻结清单确定输入；`drawing_processing` 接收 `execution_kind=drawing_processing` 并从最新分类 run 冻结整批输入；`excel_stage1` 接收 `execution_kind=excel_stage1`，由服务端解析唯一冻结 Excel 与当前拆板交接；`excel_stage2` 接收 `execution_kind=excel_stage2`，再次冻结已预检的第一阶段正式 Excel 和分类 BH 账本。四者以工作流/阶段幂等键创建或复用 Job，同事务绑定 attempt，commit 后才投递。执行端先锁定工作流行；拆板阶段一旦绑定 Job，后续项目成员始终复用该 Job，不因操作者变化重置 attempt 预算。自动阶段不能通过 completion 绕过。一般自动阶段的失败或单独取消 Job 可由相同 executions 请求复用 Job、递增 attempt、清除阶段错误并重投；响应以 `retried=true` 明确区分普通幂等复用。拆板技术失败由 worker 自动执行最多 3 个不可变整批 attempt，业务人工复核不触发技术重试；公共 Job 创建/重试端点以 `409 DXF_SPLIT_WORKFLOW_EXECUTION_REQUIRED` 拒绝绕过阶段绑定和 attempt 预算。显式取消整个流程后不可重开。

`GET /api/v1/workflows/{workflow_id}/dxf-classification` 返回最新 attempt 的分类器/schema 版本、冻结清单摘要、Job、类型汇总、逐图来源/分流 DXF 登记以及 JSON 报告和 CSV 清单。每个输出先在 MinIO 保存并建立 `files` 记录；待确认/无法读取也是明确处置，不伪装为自动分类。

`drawing_processing` 只消费已登记分类 DXF，BH/BOX 分别要求 `project_tekla_bh_dxf_v1`、`project_tekla_box_dxf_v1` 来源合同；其他类型与独立校验未通过图纸进入 `manual_review`，但整批仍处理到底。正常拆板、余量增长、两类算法报告、独立校验报告、批次 manifest 和 `BH拆板信息表.xlsx` 写入现有 MinIO bucket，并以当前 Job attempt 元数据登记。全部通过时推进 Excel；存在人工复核时 Job 成功、run 为 `completed_with_review`，工作流保持 `waiting_review`，自动产物保留。

`GET /api/v1/workflows/{workflow_id}/drawing-processing` 只返回工作流当前拆板 Job/attempt 对应的 run；尚未创建时返回 `data: null`。`GET /api/v1/workflows/{workflow_id}/drawing-processing/runs/{run_id}/manual-review-archive` 即时生成 ZIP，只包含当前 run 未通过图纸进入拆板前的分类原始 DXF，不包含候选图、报告、预览、Excel 或旧 attempt，也不把 ZIP 保存到 MinIO。

CAM 工作包、Windows CAM 和结果接纳保留同一 executions 路径，但返回 HTTP 501 `WORKFLOW_STAGE_NOT_IMPLEMENTED`；`details` 包含 `implementation_status`、`execution_mode`、`required_inputs` 和 `artifact_types`。绑定外部交接产物后，owner/engineer 可通过 completion 明确确认交接；这不代表平台执行了留白算法。

详情查询同步匹配 attempt 的 Job，成功时幂等挂接 AnalysisResult/File；拆板 `completed_with_review` 不推进。取消流程会先取消当前 active Job。分类、拆板或 Excel 功能开关关闭分别返回 `DXF_CLASSIFICATION_PIPELINE_DISABLED`、`DXF_SPLIT_PIPELINE_DISABLED` 或 `EXCEL_FINAL_PIPELINE_DISABLED`。
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
            cad_conversion_contract,
            excel_final_contract,
            workflow_contract,
            "## 运行时文档",
            "",
            "development/debug 模式启动后，访问 `/docs`、`/redoc` 或 `/openapi.json` 获取请求/响应 schema。",
            "当 `APP_ENV=production` 且 `DEBUG=false` 时，这三个运行时文档入口有意关闭；生产应使用本生成文件和版本化 OpenAPI artifact。",
            "",
        ]
    )
    return "\n".join(sections)


def main() -> int:
    API_DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    API_DOC_PATH.write_text(render(), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    main()
