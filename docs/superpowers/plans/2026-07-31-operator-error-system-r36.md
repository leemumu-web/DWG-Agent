# r36 工人报错体系与生产功能门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立不会误导工人重试的统一中文报错体系，并让 r36 在启动前后确认所有已批准生产功能实际启用。

**Architecture:** 保留后端现有错误信封和稳定错误码，在前端集中解析为错误类别、恢复动作和可重试性；所有查询与错误卡片复用同一判断。部署侧同时检查生产环境文件和容器内实际设置，使“容器健康但功能关闭”直接导致发布失败。

**Tech Stack:** React 19、TypeScript、TanStack Query、Ant Design、Playwright、FastAPI/Pydantic Settings、Bash、pytest、Docker Compose

---

实施方式：用户明确禁止子代理，因此在当前会话内逐项执行，每个任务完成后运行对应门禁并提交。

## 文件职责

- `frontend/src/shared/api/error.ts`：错误信封安全解析、中文原因、类别、恢复动作、可重试性和查询重试判断的唯一来源。
- `frontend/src/shared/components/ApiErrorAlert.tsx`：只渲染解析后的工人提示，并按 `retryable` 决定是否显示重试按钮。
- `frontend/src/app/providers.tsx`：全局查询复用统一重试判断，不自行按 HTTP 状态猜测。
- `frontend/src/features/workflows/ExcelStage2Panel.tsx`：第二阶段业务标题和预检动作，不重复定义错误规则。
- `frontend/src/features/workflows/WorkflowDetailPage.tsx`：第一阶段业务标题，不展示稳定错误码。
- `frontend/tests/e2e/workflows/excel-stage2.spec.ts`：第二阶段功能关闭、中文提示、请求次数和按钮行为浏览器回归。
- `frontend/tests/e2e/workflows/workflow-detail.spec.ts`：第一阶段不泄漏技术错误码的浏览器回归。
- `backend/app/modules/workflows/stage_execution.py`：BH 左右进同步预审核、项目/attempt/冻结清单和第一阶段对象门禁。
- `backend/app/modules/dxf_classification/persistence.py`：BH 分类账数量、上限、文件登记和不可变摘要的一致性验证。
- `backend/tests/workflows/test_workflow_production.py`：第二阶段预审核只读性、跨项目、旧清单、对象缺失和账本计数回归。
- `backend/tests/dxf_classification/test_dxf_classification_pipeline.py`：BH 分类账上限与声明计数回归。
- `scripts/lib/compose.sh`：本地生产 Compose 环境文件门禁和启动后运行时功能核验。
- `scripts/release/server-deploy.sh`：服务器发布环境文件门禁和 smoke 功能核验。
- `scripts/release/verify_runtime_features.py`：在 backend 容器内读取最终 Settings，校验已批准功能值，不读取或输出秘密。
- `backend/Dockerfile`：把运行时功能核验脚本纳入加密后端镜像。
- `backend/tests/infrastructure/test_compose.py`：生产模板已批准功能值回归。
- `backend/tests/infrastructure/test_scripts.py`：本地 Compose 环境文件与运行时核验调用回归。
- `backend/tests/infrastructure/test_server_release.py`：服务器发布门禁、镜像脚本和 smoke 顺序回归。
- `.env.docker`：本机忽略文件；补齐二阶段设置并切换 r36 镜像，不提交秘密。

### Task 1: 加固 BH 左右进同步预审核

**Files:**
- Modify: `backend/app/modules/workflows/stage_execution.py`
- Modify: `backend/app/modules/dxf_classification/persistence.py`
- Modify: `backend/tests/workflows/test_workflow_production.py`
- Modify: `backend/tests/dxf_classification/test_dxf_classification_pipeline.py`

- [ ] **Step 1: 先写冻结输入摘要漂移失败测试**

在 `_stage2_ready_workflow` 基础上修改当前 `workflow.input_batch.manifest_sha256`，调用 `preflight_excel_stage2()`，断言 409、`EXCEL_STAGE2_CLASSIFICATION_BINDING_INVALID`，并断言 Job 数量没有增加。

- [ ] **Step 2: 写第一阶段正式对象缺失与大小漂移测试**

让测试存储删除 `stage1_excel` 对象，或写入与 `StoredFile.size_bytes` 不同的对象，断言预审核返回 `EXCEL_STAGE2_STAGE1_FILE_UNAVAILABLE`，错误消息明确“第一阶段正式结果文件在存储中不可用或大小不一致”，且不创建 Job。

- [ ] **Step 3: 写 Stage1 result 元数据漂移测试**

分别修改 `AnalysisResult.result_json.workflow_artifact_type` 和 `job_attempt`，断言预审核以 `EXCEL_STAGE2_STAGE1_BINDING_INVALID` 失败，不接受只靠 artifact metadata 看似一致的结果。

- [ ] **Step 4: 写 BH 分类账声明计数和 5000 上限测试**

将 `DxfClassificationRun.type_counts_json["BH"]` 改为与实际条目数不同，断言 `load_bh_stage2_classification_batch()` 抛出 `ClassificationError`。构造 5001 个 BH 条目，断言拒绝；5000 个仍允许并生成稳定 `bh_manifest_sha256`。

- [ ] **Step 5: 运行目标测试确认当前实现失败**

Run:

```bash
cd backend
uv run pytest -q \
  tests/workflows/test_workflow_production.py -k 'excel_stage2 and (manifest or object or result_metadata)' \
  tests/dxf_classification/test_dxf_classification_pipeline.py -k 'bh_stage2 and (count or limit)'
```

Expected: 新增场景失败，证明当前门禁尚未覆盖这些漂移。

- [ ] **Step 6: 先检查必需上游产物，再做深层准备**

在 `preflight_excel_stage2()` 与 `prepare_stage_execution()` 的二阶段分支把 `require_stage_inputs(workflow, "excel_stage2")` 放到 `_prepare_excel_stage2()` 前面，确保缺少正式上游产物时优先返回清晰输入错误。

- [ ] **Step 7: 加固第一阶段正式来源链**

在 `_prepare_excel_stage2()` 中：

```python
result_meta = result.result_json if isinstance(result.result_json, dict) else {}
```

要求 `workflow_artifact_type == "stage1_excel"`、`job_attempt == stage1_job.attempt`。通过存储 `stat_object()` 验证正式 Excel 对象存在且 `size_bytes` 与登记相同；缺失、存储异常或大小不一致统一映射为安全的 `EXCEL_STAGE2_STAGE1_FILE_UNAVAILABLE`，不暴露 bucket/key。

- [ ] **Step 8: 绑定分类账到当前冻结输入**

保留 `_source_batch`，并要求：

```python
classification.input_manifest_sha256 == source_batch.manifest_sha256
classification_params.get("input_manifest_sha256") == source_batch.manifest_sha256
```

失败时返回 `EXCEL_STAGE2_CLASSIFICATION_BINDING_INVALID`。在成功预检 checks 中新增“分类账与当前冻结输入一致”和“BH 文件登记账本已冻结”，避免宣称几何已解析。

- [ ] **Step 9: 在分类领域校验 BH 账本数量与上限**

在 `load_bh_stage2_classification_batch()` 中定义 `MAX_BH_STAGE2_INPUTS = 5000`，要求 `type_counts_json["BH"]` 是非负整数且等于查询得到的 BH 行数，并拒绝超过上限。继续保留文件 ID 去重、规格/type source、状态、扩展名、文件名和 SHA-256 检查。

- [ ] **Step 10: 运行二阶段后端回归**

Run:

```bash
cd backend
uv run pytest -q tests/workflows/test_workflow_production.py \
  tests/dxf_classification/test_dxf_classification_pipeline.py \
  tests/excel_processing/test_excel_stage2_execution.py
```

Expected: 全部通过；预审核仍不创建 Job，worker 的冻结摘要二次校验、逐图读取、重复零件号阻断和失败诊断全部保持通过。

- [ ] **Step 11: 提交预审核加固**

```bash
git add backend/app/modules/workflows/stage_execution.py \
  backend/app/modules/dxf_classification/persistence.py \
  backend/tests/workflows/test_workflow_production.py \
  backend/tests/dxf_classification/test_dxf_classification_pipeline.py
git commit -m "fix: harden BH stage2 preflight lineage"
```

### Task 2: 用浏览器测试锁定“功能关闭不得重试”

**Files:**
- Modify: `frontend/tests/e2e/workflows/excel-stage2.spec.ts`
- Modify: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`

- [ ] **Step 1: 添加第二阶段确定性 503 回归**

在 `excel-stage2.spec.ts` 增加场景，预检返回：

```ts
{
  error: {
    code: 'EXCEL_STAGE2_PIPELINE_DISABLED',
    message: 'Excel 第二阶段处理服务当前未启用。',
    details: {},
  },
  meta: { request_id: 'stage2-disabled-r36' },
}
```

记录预检请求数，并断言：

```ts
await expect(page.getByText('Excel 第二阶段处理服务当前未启用。')).toBeVisible();
await expect(page.getByText('当前部署未开启 Excel 第二阶段处理，请联系管理员检查服务配置。')).toBeVisible();
await expect(page.getByText('EXCEL_STAGE2_PIPELINE_DISABLED')).toHaveCount(0);
await expect(page.getByText(/稍后重试一次/)).toHaveCount(0);
await expect(page.getByRole('button', { name: '重新检查' })).toHaveCount(0);
await expect.poll(() => preflightRequests).toBe(1);
```

- [ ] **Step 2: 添加第一阶段标题不泄漏错误码回归**

在 `workflow-detail.spec.ts` 为第一阶段预检构造 `EXCEL_STAGE1_PIPELINE_DISABLED`，断言固定标题“Excel 第一阶段运行前检查未通过”可见，稳定错误码和“稍后重试一次”不可见，且没有“重新检查”按钮。

- [ ] **Step 3: 运行测试确认当前实现失败**

Run:

```bash
cd frontend
PLAYWRIGHT_FRONTEND_BASE_URL=http://127.0.0.1:18080 \
  npx playwright test tests/e2e/workflows/excel-stage2.spec.ts \
  tests/e2e/workflows/workflow-detail.spec.ts --grep '未启用|错误码'
```

Expected: FAIL；当前错误建议包含“稍后重试一次”，第二阶段仍出现“重新检查”，第一阶段标题可能展示错误码。

- [ ] **Step 4: 提交失败测试**

```bash
git add frontend/tests/e2e/workflows/excel-stage2.spec.ts \
  frontend/tests/e2e/workflows/workflow-detail.spec.ts
git commit -m "test: define operator feature-disabled errors"
```

### Task 3: 建立统一错误语义和查询重试判断

**Files:**
- Modify: `frontend/src/shared/api/error.ts`
- Modify: `frontend/src/shared/api/index.ts`
- Modify: `frontend/src/app/providers.tsx`

- [ ] **Step 1: 扩展解析类型**

在 `error.ts` 定义并导出：

```ts
export type ApiErrorKind =
  | 'input'
  | 'authentication'
  | 'authorization'
  | 'not_found'
  | 'conflict'
  | 'feature_disabled'
  | 'capacity'
  | 'transient'
  | 'server'
  | 'unknown';

export interface ParsedApiError {
  message: string;
  status?: number;
  code?: string;
  requestId?: string;
  failure?: ExcelInputFailure;
  workflowId?: number;
  kind: ApiErrorKind;
  retryable: boolean;
}
```

- [ ] **Step 2: 实现稳定错误优先的分类**

增加 `isFeatureDisabledCode()`，识别 `*_PIPELINE_DISABLED`、`AGENT_DISABLED` 和 `REMNANT_INVENTORY_DISABLED`。增加 `classifyApiError(code, status, responseReceived)`：稳定功能关闭优先；400/413/415/422 为输入，401 为登录，403 为权限，404 为不存在，409 为冲突，429 与存储满为容量；无响应、502、504和未知 503 为临时；其余 5xx 为服务器错误。

- [ ] **Step 3: 给功能关闭配置精确中文和恢复动作**

至少增加：

```ts
EXCEL_STAGE1_PIPELINE_DISABLED: 'Excel 第一阶段处理服务当前未启用。',
EXCEL_STAGE2_PIPELINE_DISABLED: 'Excel 第二阶段处理服务当前未启用。',
DXF_CLASSIFICATION_PIPELINE_DISABLED: 'DXF 分类服务当前未启用。',
DXF_SPLIT_PIPELINE_DISABLED: 'DXF 拆板服务当前未启用。',
REMNANT_INVENTORY_DISABLED: '余料库功能当前未启用。',
```

`apiErrorRecovery()` 对 `feature_disabled` 返回具体管理员动作，不返回等待或重试。对 500 返回保留请求编号并联系管理员；只有 `transient` 返回刷新状态后有限重试。

- [ ] **Step 4: 导出统一查询重试函数**

```ts
export function shouldRetryApiQuery(failureCount: number, error: unknown): boolean {
  return failureCount < 2 && parseApiError(error).retryable;
}
```

在 `frontend/src/shared/api/index.ts` 导出该函数，并在 `providers.tsx` 替换内联 HTTP 判断：

```ts
queries: {
  refetchOnWindowFocus: false,
  retry: shouldRetryApiQuery,
  retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 10000),
}
```

- [ ] **Step 5: 运行类型和构建门禁**

Run:

```bash
cd frontend
npm run build
```

Expected: `check:architecture`、`tsc -b`、`vite build` 全部通过。

- [ ] **Step 6: 提交统一错误语义**

```bash
git add frontend/src/shared/api/error.ts frontend/src/shared/api/index.ts \
  frontend/src/app/providers.tsx
git commit -m "feat: classify operator API recovery actions"
```

### Task 4: 接入错误卡片和 Excel 第一、二阶段

**Files:**
- Modify: `frontend/src/shared/components/ApiErrorAlert.tsx`
- Modify: `frontend/src/features/workflows/ExcelStage2Panel.tsx`
- Modify: `frontend/src/features/workflows/WorkflowDetailPage.tsx`
- Modify: `frontend/src/shared/styles/index.css`

- [ ] **Step 1: 让错误卡片只展示有效动作**

在 `ApiErrorAlert.tsx` 计算：

```ts
const canRetry = Boolean(onRetry && parsed.retryable);
```

只有 `canRetry` 时展示重试按钮；`extraAction` 仍可独立显示。继续只显示 `parsed.message`、`apiErrorRecovery(parsed)` 和请求编号，不显示 `parsed.code`。

- [ ] **Step 2: 固定 Excel 阶段中文标题**

第二阶段保留“第二阶段运行前检查未通过”，其 `onRetry` 可以继续传入，由通用组件决定是否展示。第一阶段把：

```tsx
title={excelPreflightError.code ?? '运行前检查未通过'}
```

替换为：

```tsx
title="Excel 第一阶段运行前检查未通过"
```

- [ ] **Step 3: 精炼错误卡片视觉层级**

在 `index.css` 保留现有工业化红色左边框，增加说明和动作区间距，确保窄屏换行，不增加大面积动画或技术详情展开区。

- [ ] **Step 4: 重建前端并运行目标浏览器测试**

Run:

```bash
cd frontend
npm run build
PLAYWRIGHT_FRONTEND_BASE_URL=http://127.0.0.1:18080 \
  npx playwright test tests/e2e/workflows/excel-stage2.spec.ts \
  tests/e2e/workflows/workflow-detail.spec.ts
```

Expected: build 通过；所有 Excel 第一/二阶段浏览器场景通过，新场景只请求一次且没有无效重试按钮或技术错误码。

- [ ] **Step 5: 提交 UI 接入**

```bash
git add frontend/src/shared/components/ApiErrorAlert.tsx \
  frontend/src/features/workflows/ExcelStage2Panel.tsx \
  frontend/src/features/workflows/WorkflowDetailPage.tsx \
  frontend/src/shared/styles/index.css \
  frontend/tests/e2e/workflows/excel-stage2.spec.ts \
  frontend/tests/e2e/workflows/workflow-detail.spec.ts
git commit -m "fix: show actionable workflow errors"
```

### Task 5: 阻止生产功能开关漂移

**Files:**
- Create: `scripts/release/verify_runtime_features.py`
- Modify: `scripts/lib/compose.sh`
- Modify: `scripts/release/server-deploy.sh`
- Modify: `backend/Dockerfile`
- Modify: `backend/tests/infrastructure/test_compose.py`
- Modify: `backend/tests/infrastructure/test_scripts.py`
- Modify: `backend/tests/infrastructure/test_server_release.py`

- [ ] **Step 1: 先写生产模板和脚本失败测试**

扩展 `test_server_example_enables_approved_shipping_pipelines()`，加入：

```python
"EXCEL_STAGE2_PIPELINE_ENABLED",
"REMNANT_INVENTORY_ENABLED",
```

在 `test_scripts.py` 增加临时 env-file 场景：缺失 `EXCEL_STAGE2_PIPELINE_ENABLED`、设置为 `false`、余料库缺失或为 `false` 都必须非零退出；完整批准矩阵通过。断言 `compose_smoke` 调用 `/app/scripts/release/verify_runtime_features.py`。

在 `test_server_release.py` 断言 `server_validate_runtime` 验证完整批准矩阵，`server_smoke` 在余料库真实闭环前调用运行时功能核验脚本。

- [ ] **Step 2: 运行测试确认缺口**

Run:

```bash
cd backend
uv run pytest -q \
  tests/infrastructure/test_compose.py::TestCompose::test_server_example_enables_approved_shipping_pipelines \
  tests/infrastructure/test_scripts.py \
  tests/infrastructure/test_server_release.py
```

Expected: 新增的 env-file 与运行时核验断言失败。

- [ ] **Step 3: 实现容器内运行时核验脚本**

`verify_runtime_features.py` 导入 `settings`，检查：

```python
EXPECTED = {
    "dxf_pipeline_enabled": True,
    "dxf2dwg_pipeline_enabled": True,
    "dxf2excel_pipeline_enabled": False,
    "dxf_classification_pipeline_enabled": True,
    "dxf_split_pipeline_enabled": True,
    "excel_final_pipeline_enabled": True,
    "excel_stage2_pipeline_enabled": True,
    "remnant_inventory_enabled": True,
}
```

不一致时把字段名、期望布尔值和实际布尔值写到 stderr 并返回非零；一致时只输出不含秘密的 JSON。不得输出整个环境或 Settings。

- [ ] **Step 4: 实现 env-file 生产门禁**

在两套 Bash 脚本分别实现逐项精确读取；值统一转小写，只接受上表精确值。`APP_ENV=production` 时必须执行；服务器发布始终按生产矩阵执行。错误信息指出变量和期望值，不打印秘密。

- [ ] **Step 5: 把运行时核验加入镜像和 smoke**

在 `backend/Dockerfile` 将脚本复制到 `/app/scripts/release/verify_runtime_features.py`。在 `compose_smoke` 与 `server_smoke` 中执行：

```bash
... exec -T backend-api \
  python /app/scripts/release/verify_runtime_features.py
```

服务器 smoke 顺序为：网关/就绪 → 运行时功能矩阵 → 余料库 MySQL/MinIO 真实闭环。

- [ ] **Step 6: 运行基础设施测试**

Run:

```bash
cd backend
uv run pytest -q tests/infrastructure/test_compose.py \
  tests/infrastructure/test_scripts.py \
  tests/infrastructure/test_server_release.py \
  tests/infrastructure/test_celery_minio_deployment.py
```

Expected: 全部通过。

- [ ] **Step 7: 提交生产门禁**

```bash
git add scripts/release/verify_runtime_features.py scripts/lib/compose.sh \
  scripts/release/server-deploy.sh backend/Dockerfile \
  backend/tests/infrastructure/test_compose.py \
  backend/tests/infrastructure/test_scripts.py \
  backend/tests/infrastructure/test_server_release.py
git commit -m "fix: gate production runtime feature drift"
```

### Task 6: 完整回归与 r36 加密发布

**Files:**
- Modify (ignored, no commit): `.env.docker`
- Generate (untracked): `releases/dwg-agent-server-production-20260731-r36.tar.gz.gpg`
- Generate (untracked): matching deploy script and SHA-256 files

- [ ] **Step 1: 补齐本机生产配置**

保留当前所有密钥和连接配置，只补齐：

```dotenv
EXCEL_STAGE2_PIPELINE_ENABLED=true
EXCEL_STAGE2_TIMEOUT_SECONDS=7200
EXCEL_STAGE2_WORK_ROOT=/app/var/excel-stage2-work
```

确认余料库为 `true`，设置 `HTTP_PORT=18080`，将四个发布镜像标签切换为 `server-production-20260731-r36`。

- [ ] **Step 2: 运行完整代码门禁**

Run:

```bash
cd backend
uv run pytest -q
cd ../frontend
npm run build
```

Expected: 后端零失败；前端架构、类型和生产构建通过。

- [ ] **Step 3: 构建并加密 r36**

按现有 r35 发布命令调用 `scripts/release.sh build` 和 `bundle`，版本固定为 `server-production-20260731-r36`。确认镜像归档验证器拒绝业务 Python 源码，GPG 外层校验和通过，发布包不含 `.env.docker` 和数据库内容。

- [ ] **Step 4: 替换本机旧容器**

使用当前 Compose 项目执行 r36 `up`/`recover`，允许删除并重建 r35 容器与 orphan 容器，但禁止 `down -v`。等待 15/15 服务健康。

- [ ] **Step 5: 运行真实功能 smoke**

验证：

```bash
curl -fsS http://127.0.0.1:18080/nginx-health
curl -fsS http://127.0.0.1:18080/health/ready
```

并通过容器内脚本确认功能矩阵；运行余料库真实 MySQL/MinIO 闭环；使用现有账号读取工作流 5、6、8，确认工作流 8 的 Excel 第二阶段预检不再返回 `EXCEL_STAGE2_PIPELINE_DISABLED`，工作流 5/6 现有结果仍可下载。

- [ ] **Step 6: 运行浏览器与多账号回归**

Run:

```bash
cd frontend
PLAYWRIGHT_FRONTEND_BASE_URL=http://127.0.0.1:18080 \
PLAYWRIGHT_API_BASE_URL=http://127.0.0.1:18080 \
npx playwright test tests/e2e/contracts tests/e2e/workflows \
  tests/e2e/remnant-inventory
```

Expected: 管理员和操作员权限边界、工作流、错误提示、下载与余料库场景零失败；退出登录不取消已入队任务。

- [ ] **Step 7: 清理 r35 并提交发布代码**

确认 r36 15/15 健康且业务 smoke 通过后，删除已停止的 r35 容器与 r35 镜像；再次列出 `dwg-agent_mysql_data`、`dwg-agent_minio_data`、`dwg-agent_app_var` 证明业务卷仍存在。提交剩余受跟踪变更，不提交 `.env.docker`、`releases/`、`output/` 或样本数据。

- [ ] **Step 8: 最终证据清单**

记录并汇报：提交、测试计数、前端构建、15 个容器健康状态、运行时功能矩阵、工作流 5/6/8、余料库闭环、r36 加密包路径与 SHA-256、r35 容器/镜像清理情况、数据卷保留情况。任何未验证项必须明确标出，不能用代码阅读代替通过结论。
