# PR #1 Selective Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在当前 `main` 上安全吸收 PR #1 的 DXF 预览、Excel Final 前端增强、流程桥接、字段扩容和开发配置思路，同时保留既有事务、权限、分页和数据控制台能力。

**Architecture:** DXF 预览采用 ezdxf SVG 记录器、MySQL/MinIO 登记缓存和认证 Blob 输出；Excel Final 采用权限过滤聚合与服务端分页；所有对象写入和输出复用现有文件传输流水。历史迁移和 PR 分支代码不直接合并。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、Alembic、MySQL、MinIO、ezdxf、Pillow、React 19、TypeScript、Ant Design、TanStack Query、Playwright、Docker Compose。

---

## 文件职责映射

- `backend/app/services/dxf_preview_service.py`：有界读取后的 DXF 解析、复杂度检查、SVG 生成、缓存查找和缓存写入。
- `backend/app/api/v1/files_api.py`：预览元数据和认证内容端点、权限、审计、出站流水。
- `backend/app/schemas/file_schema.py`：DXF 预览响应类型。
- `backend/app/api/v1/excel_final_api.py`：权限过滤概览与构件分页。
- `backend/app/models/excel_final.py`：扩容后的标识字段。
- `backend/migrations/versions/9c4e7b1a2d60_widen_excel_final_identifiers.py`：从当前 head 延伸的唯一新迁移。
- `frontend/src/components/DxfPreviewModal.tsx` 与 `.css`：认证 SVG Blob、缩放平移和元数据界面。
- `frontend/src/features/files/excel-final/`：概览、工具、批次详情三个聚焦组件。
- `frontend/src/features/files/ExcelFinalPage.tsx`：页面编排、上传/任务/批次与 URL job 跟踪。
- `frontend/src/features/files/Dxf2ExcelPage.tsx`：成功结果提交到 Excel Final。
- `frontend/src/components/ConversionPage.tsx`：源/结果 DXF 预览入口。
- `frontend/src/api/files.api.ts`、`excel-final.api.ts`、`types/*.ts`：前后端契约。
- `compose.dev.yaml`：当前拓扑的后端热更新覆盖。
- `docs/*.md`：API、开发、部署、数据库、工作流与验证证据。

### Task 1: 固化 PR 审查基线与依赖契约

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Test: `backend/tests/test_dxf_preview_service.py`

- [ ] **Step 1: 写依赖失败测试**

新增测试导入 `ezdxf.addons.drawing.svg.SVGBackend` 和 `PIL`，并断言服务模块可导入。当前环境缺少 Pillow，测试应以 `ModuleNotFoundError` 或服务模块不存在失败。

- [ ] **Step 2: 验证红灯**

Run: `cd backend && uv run pytest -q tests/test_dxf_preview_service.py`

Expected: FAIL，原因是 `dxf_preview_service` 或 Pillow 尚不存在。

- [ ] **Step 3: 声明直接依赖并更新锁**

在 `dependencies` 中加入：

```toml
"ezdxf>=1.4,<2",
"Pillow>=11,<13",
```

运行 `uv lock`，再运行 `uv lock --check`，确保不依赖 PR 中不一致的锁文件。

- [ ] **Step 4: 验证依赖可导入**

Run: `cd backend && uv sync --frozen && uv run python -c "from PIL import Image; from ezdxf.addons.drawing.svg import SVGBackend"`

Expected: exit 0。

### Task 2: DXF SVG 解析和安全渲染

**Files:**
- Create: `backend/app/services/dxf_preview_service.py`
- Test: `backend/tests/test_dxf_preview_service.py`

- [ ] **Step 1: 写纯服务红灯测试**

覆盖以下行为：

```python
def test_render_dxf_returns_safe_svg_and_metadata():
    rendered = render_dxf_to_svg(_minimal_dxf_bytes())
    assert rendered.payload.startswith(b"<?xml")
    assert b"<svg" in rendered.payload
    assert rendered.content_type == "image/svg+xml"
    assert rendered.document_entities > 0
    assert "0" in rendered.layers
    assert not any(token in rendered.payload.lower() for token in FORBIDDEN_SVG_TOKENS)

def test_document_entity_limit_counts_block_entities(monkeypatch):
    monkeypatch.setattr(service, "MAX_DXF_ENTITIES", 2)
    with pytest.raises(AppHTTPException) as exc:
        render_dxf_to_svg(_dxf_with_block_entities())
    assert exc.value.detail["code"] == "DXF_TOO_COMPLEX"

def test_output_size_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(service, "MAX_PREVIEW_BYTES", 32)
    with pytest.raises(AppHTTPException) as exc:
        render_dxf_to_svg(_minimal_dxf_bytes())
    assert exc.value.detail["code"] == "DXF_PREVIEW_TOO_LARGE"
```

- [ ] **Step 2: 运行测试并确认正确失败**

Run: `cd backend && uv run pytest -q tests/test_dxf_preview_service.py`

Expected: FAIL，缺少渲染函数和类型。

- [ ] **Step 3: 实现最小安全 SVG 渲染**

实现不可变 `RenderedDxfPreview`，使用临时 `.dxf` 文件、`ezdxf.readfile()`、存活 `entitydb` 计数、`SVGBackend`、`ImagePolicy.IGNORE`、有限 hatch timeout 和自动页面。渲染后检查：

```python
FORBIDDEN_SVG_TOKENS = (
    b"<script", b"<foreignobject", b" href=", b"xlink:",
    b"<!doctype", b"<!entity",
)
```

边界从 recorder player 的原始 bbox 获取；空图或非有限边界返回零边界。

- [ ] **Step 4: 验证绿灯与真实样例性能**

Run: `cd backend && uv run pytest -q tests/test_dxf_preview_service.py`

Run: 使用 `Stages/dxf2dwg/samples/input` 中约 5 MB 样例调用渲染函数并记录耗时、实体数和 SVG 字节数。

Expected: 测试 PASS；真实样例明显低于 PR 的 27 秒基线。

### Task 3: DXF 缓存、MinIO/MySQL 登记和并发二次检查

**Files:**
- Modify: `backend/app/services/dxf_preview_service.py`
- Test: `backend/tests/test_dxf_preview_service.py`
- Test: `backend/tests/test_file_transfer_service.py`

- [ ] **Step 1: 写缓存和事务红灯测试**

覆盖：

```python
def test_preview_generation_registers_file_and_generated_transfer(...):
    result = get_or_create_dxf_preview(db, source, payload, request_id="req-1")
    db.commit()
    assert result.preview_file.file_ext == ".svg"
    assert result.preview_file.batch_name.startswith(f"dxf-preview:{source.id}:")
    transfer = _transfer_for_file(db, result.preview_file.id)
    assert (transfer.direction, transfer.operation, transfer.status) == (
        "internal", "preview_generate", "succeeded"
    )

def test_minio_style_cache_hit_uses_stat_not_local_path(...):
    storage.local_path.return_value = None
    storage.stat_object.return_value = StorageObjectInfo(...)
    result = get_or_create_dxf_preview(...)
    assert result.cached is True
    storage.put_fileobj.assert_not_called()

def test_missing_cached_object_is_replaced(...):
    storage.stat_object.side_effect = [StorageObjectNotFound(...), StorageObjectInfo(...)]
    result = get_or_create_dxf_preview(...)
    assert old_preview.status == "deleted"
    assert result.preview_file.id != old_preview.id
```

- [ ] **Step 2: 验证红灯**

Run: `cd backend && uv run pytest -q tests/test_dxf_preview_service.py tests/test_file_transfer_service.py`

Expected: FAIL，缺少缓存服务行为。

- [ ] **Step 3: 实现缓存查询和写入**

以 `batch_name=dxf-preview:{source_id}:{sha256[:16]}` 查询最新可用 SVG，使用 `storage.stat_object()` 验证。缓存未命中时渲染，随后 `SELECT ... FOR UPDATE` 锁源文件并二次查询；仍未命中才调用：

```python
save_bytes_as_file(
    db,
    bucket=settings.minio_bucket_reports,
    storage_key=f"previews/dxf/{source.id}/{source.sha256[:16]}/{uuid4().hex}.svg",
    original_name=preview_name,
    file_ext=".svg",
    content_type="image/svg+xml",
    payload=rendered.payload,
    uploaded_by=None,
    batch_name=batch_name,
    transfer_direction="internal",
    transfer_operation="preview_generate",
    request_id=request_id,
)
```

- [ ] **Step 4: 验证绿灯**

Run: `cd backend && uv run pytest -q tests/test_dxf_preview_service.py tests/test_file_transfer_service.py`

Expected: PASS。

### Task 4: 预览 API、权限、认证内容流和出站流水

**Files:**
- Modify: `backend/app/api/v1/files_api.py`
- Modify: `backend/app/schemas/file_schema.py`
- Test: `backend/tests/test_files_api.py`
- Test: `backend/tests/test_file_transfer_service.py`
- Test: `backend/tests/test_security_boundaries.py`

- [ ] **Step 1: 写端点红灯测试**

新增：非 DXF 返回 415；DB 声明超限时不调用 `iter_file()`；无权用户返回 403；元数据返回 `preview_file_id` 和专用 `content_url`；内容端点拒绝不匹配预览 ID；成功内容流产生 `operation=preview` 的出站流水并按实际字节完成。

- [ ] **Step 2: 运行聚焦测试确认失败**

Run: `cd backend && uv run pytest -q tests/test_files_api.py tests/test_file_transfer_service.py tests/test_security_boundaries.py -k dxf_preview`

Expected: FAIL，路由不存在。

- [ ] **Step 3: 实现元数据端点**

在读取对象前检查 `.dxf` 和 `size_bytes`；用 `storage.iter_file()` 有界读取；调用缓存服务；写 `files.dxf_preview_generate` 或 `files.dxf_preview_cache_hit` 审计；提交后返回元数据。

- [ ] **Step 4: 实现认证内容端点**

专用内容端点重新校验源文件权限、预览批次标记和对象状态，准备：

```python
TransferSpec(
    direction="outbound",
    operation="preview",
    actor_user_id=current_user.id,
    file_id=preview.id,
    bucket=preview.bucket,
    storage_key=preview.storage_key,
    expected_bytes=object_info.size_bytes,
)
```

提交后返回 `StreamingResponse(settle_stream(...))`，设置 `Content-Length`、`X-Content-Type-Options`、私有缓存和 SVG CSP。

- [ ] **Step 5: 验证聚焦测试**

Run: `cd backend && uv run pytest -q tests/test_files_api.py tests/test_file_transfer_service.py tests/test_security_boundaries.py -k dxf_preview`

Expected: PASS。

### Task 5: DXF 前端认证预览器与入口

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/src/types/file.ts`
- Modify: `frontend/src/api/files.api.ts`
- Create: `frontend/src/components/DxfPreviewModal.tsx`
- Create: `frontend/src/components/DxfPreviewModal.css`
- Modify: `frontend/src/components/ConversionPage.tsx`
- Test: `frontend/tests/e2e/dxf-preview.spec.ts`

- [ ] **Step 1: 写浏览器契约红灯测试**

测试登录后进入 DWG→DXF 页面，模拟/创建 DXF 源或结果，点击“预览 DXF”，断言元数据请求和认证内容请求成功、弹窗含实体/图层、缩放/重置按钮可操作，关闭后 object URL 被释放且无控制台错误。

- [ ] **Step 2: 验证红灯**

Run: `cd frontend && npx playwright test tests/e2e/dxf-preview.spec.ts`

Expected: FAIL，预览按钮或组件不存在。

- [ ] **Step 3: 实现 API 与 Blob 生命周期**

`fetchDxfPreview(fileId)` 获取元数据；`fetchDxfPreviewBlob(contentUrl, signal)` 使用 `apiClient.get(..., {responseType:'blob'})`，组件创建并在替换、关闭和卸载时撤销 object URL。

- [ ] **Step 4: 实现可访问、响应式预览器**

使用 `react-zoom-pan-pinch`，提供放大、缩小、重置、重新加载、下载源文件按钮；桌面显示元数据侧栏，小屏折叠到图像下方；所有图标按钮有 `aria-label` 和 tooltip。

- [ ] **Step 5: 接入源 DXF 和转换结果 DXF**

源扩展名为 `.dxf` 时直接预览源文件；结果扩展名为 `.dxf` 且任务成功时通过 `getJobResults()` 定位结果文件再打开预览。

- [ ] **Step 6: 验证前端聚焦门禁**

Run: `cd frontend && npm ci && npm run build && npx playwright test tests/e2e/dxf-preview.spec.ts`

Expected: build 与测试 PASS。

### Task 6: Excel Final 概览、构件分页和字段扩容

**Files:**
- Modify: `backend/app/api/v1/excel_final_api.py`
- Modify: `backend/app/models/excel_final.py`
- Create: `backend/migrations/versions/9c4e7b1a2d60_widen_excel_final_identifiers.py`
- Test: `backend/tests/test_excel_final_api.py`
- Test: `backend/tests/test_excel_final_models.py`
- Test: `backend/tests/test_job_access.py`

- [ ] **Step 1: 写后端红灯测试**

断言概览只聚合当前用户可读批次；空集合返回零；构件端点返回分页；模型长度为 512/255/128；Alembic 仍只有一个 head。

- [ ] **Step 2: 验证红灯**

Run: `cd backend && uv run pytest -q tests/test_excel_final_api.py tests/test_excel_final_models.py tests/test_job_access.py -k 'overview or component or string'`

Expected: FAIL，概览不存在且列宽仍为旧值。

- [ ] **Step 3: 实现权限过滤聚合与构件分页**

概览查询从 `ExcelFinalBatch JOIN Job` 聚合；非全局用户应用 `job_read_filter(current_user)`。构件列表接受 `page/page_size`，使用 `paginate_scalars()` 返回 `page_response()`。

- [ ] **Step 4: 新建当前 head 后迁移并同步模型**

迁移只执行六个 `alter_column`，`down_revision` 指向执行时当前 head；禁止修改 `3480bd86ddc3`。模型长度与迁移一致。

- [ ] **Step 5: 验证后端和迁移绿灯**

Run: `cd backend && uv run pytest -q tests/test_excel_final_api.py tests/test_excel_final_models.py tests/test_job_access.py`

Run: `cd backend && uv run alembic heads && uv run alembic check`

Expected: PASS 且只有一个 head。

### Task 7: Excel Final 前端增强与组件拆分

**Files:**
- Modify: `frontend/src/api/excel-final.api.ts`
- Modify: `frontend/src/types/excel-final.ts`
- Modify: `frontend/src/features/files/ExcelFinalPage.tsx`
- Create: `frontend/src/features/files/excel-final/ExcelFinalOverview.tsx`
- Create: `frontend/src/features/files/excel-final/ExcelFinalTools.tsx`
- Create: `frontend/src/features/files/excel-final/ExcelFinalBatchDrawer.tsx`
- Test: `frontend/tests/e2e/excel-final-flow.spec.ts`

- [ ] **Step 1: 扩展浏览器红灯测试**

覆盖健康提示、准确概览、搜索提交/分页/清除、比重错误反馈、批次详情、零件详情、构件分页、任务结果预览和 URL `job_id` 跟踪。

- [ ] **Step 2: 验证红灯**

Run: `cd frontend && npx playwright test tests/e2e/excel-final-flow.spec.ts`

Expected: 新能力断言 FAIL。

- [ ] **Step 3: 扩展 API 和类型**

新增 `getExcelFinalOverview()`、`getExcelFinalHealth()`、`searchExcelFinalParts()`、`lookupExcelFinalWeight()`、`getExcelFinalPart()`、分页 `listExcelFinalComponents()`，类型严格匹配后端字段。

- [ ] **Step 4: 实现三个聚焦组件**

概览组件只处理健康和统计；工具组件使用 draft/applied 状态并在清除时删除旧结果；详情组件管理零件/构件分页和零件详情。

- [ ] **Step 5: 重组页面编排**

保留当前上传、近期任务、重试、批次服务端分页；增加结果 Excel 预览；读取 `job_id` 查询参数设置活动任务；所有异步失败展示 Alert 或 message。

- [ ] **Step 6: 验证前端绿灯**

Run: `cd frontend && npm run build && npx playwright test tests/e2e/excel-final-flow.spec.ts`

Expected: PASS。

### Task 8: DXF→Excel 到 Excel Final 流程桥接

**Files:**
- Modify: `frontend/src/api/excel-final.api.ts`
- Modify: `frontend/src/features/files/Dxf2ExcelPage.tsx`
- Modify: `frontend/src/features/files/ExcelFinalPage.tsx`
- Test: `frontend/tests/e2e/excel-final-flow.spec.ts`

- [ ] **Step 1: 写桥接红灯测试**

从成功的 DXF→Excel 批次触发“生成零件清单”，断言只发送一次 `/excel-final/process`，按钮处于 loading，随后导航至 `/files/excel-final?job_id=<id>` 并开始轮询。

- [ ] **Step 2: 验证红灯**

Run: `cd frontend && npx playwright test tests/e2e/excel-final-flow.spec.ts -g 'DXF.*Excel Final'`

Expected: FAIL，操作不存在。

- [ ] **Step 3: 实现防重复提交和导航**

静态导入 `processExcelFinalFile()`；用 `Set<string>` 跟踪提交中的批次；成功后 `navigate('/files/excel-final?job_id=' + job.id)`；失败恢复按钮并展示后端错误。

- [ ] **Step 4: 验证绿灯**

Run: `cd frontend && npx playwright test tests/e2e/excel-final-flow.spec.ts -g 'DXF.*Excel Final'`

Expected: PASS。

### Task 9: 当前拓扑的开发 Compose

**Files:**
- Create: `compose.dev.yaml`
- Modify: `backend/tests/test_compose.py`
- Modify: `docs/development.md`
- Modify: `docs/deployment.md`

- [ ] **Step 1: 写 Compose 红灯测试**

断言覆盖文件使用容器 8010、宿主只绑定 `127.0.0.1`、包含 report/dxf/dxf2dwg/dxf2excel/excel-final worker 源码挂载，不改变 MySQL/MinIO 内部网络。

- [ ] **Step 2: 验证红灯**

Run: `cd backend && uv run pytest -q tests/test_compose.py -k dev`

Expected: FAIL，文件不存在。

- [ ] **Step 3: 实现开发覆盖并写使用文档**

后端命令使用 `uvicorn --reload --host 0.0.0.0 --port 8010`，保留迁移和种子顺序；不提交 `.env.docker` 或测试数据。

- [ ] **Step 4: 验证 Compose 合并配置**

Run: `docker compose -f compose.yaml -f compose.dev.yaml config --quiet`

Expected: exit 0。

### Task 10: 文档、生成物和全量验收

**Files:**
- Modify: `README.md`
- Modify: `docs/api.md`（生成）
- Modify: `docs/architecture.md`
- Modify: `docs/database.md`
- Modify: `docs/development.md`
- Modify: `docs/operations.md`
- Modify: `docs/processing-pipelines.md`
- Modify: `docs/security.md`
- Modify: `docs/workflow-verification.md`

- [ ] **Step 1: 更新规范文档并生成 API**

先完成 route/test，再运行 `make docs-generate`。记录 SVG 而非 PNG、认证 Blob、缓存登记、流水操作、字段迁移、开发 Compose、默认 flag 和验证日期。

- [ ] **Step 2: 运行后端完整门禁**

Run:

```bash
cd backend
uv run ruff check app tests ../tests/run_full_verify.py
uv run pytest -q
uv run alembic check
cd ..
bash scripts/db.sh migration-test
bash infra/verify.sh
docker compose config --quiet
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
```

Expected: 全部 PASS。

- [ ] **Step 3: 运行 Stage 与前端完整门禁**

Run:

```bash
cd Stages/dwg2dxf && uv run pytest -q && cd ../..
cd Stages/dxf2dwg && uv run pytest -q && cd ../..
cd Stages/excel_final && uv run pytest -q multi_split/tests && cd ../..
cd frontend
npm ci
npm run build
npx playwright test
```

Expected: 全部 PASS，只有已有且条件明确的缺样例 skip。

- [ ] **Step 4: 运行真实 MySQL/MinIO/DXF 浏览器验证**

上传仓库真实 DXF 样例，确认首次生成和二次缓存；在数据控制台确认预览生成/输出流水、MySQL 文件行和 MinIO 对象；截图保存到 `output/playwright/`。

- [ ] **Step 5: 自审最终差异**

Run: `git diff --check`、`git status --short`、按文件查看 diff。确认未合入 PR 的历史迁移、旧端口、根目录 SUMMARY 或无关文件，确认文档计数与当前 OpenAPI/Alembic 状态一致。

## 计划自审结果

- 设计中的 DXF 安全渲染、认证读取、MinIO/MySQL 登记、出站流水、Excel Final 全局概览、分页、流程桥接、列宽迁移、开发 Compose 和文档均有对应任务。
- 未包含直接 merge、修改远程 PR、回改历史迁移或恢复过期状态文档。
- 前后端字段统一使用 `preview_file_id`、`content_url`、`document_entities`、`modelspace_entities`；Excel 构件列表统一使用 `PageEnvelope<ExcelFinalComponent>`。
- 计划不依赖子代理；根据用户明确授权在当前会话内顺序执行并在自然检查点复核。
