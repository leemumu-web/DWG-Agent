# 前端下载功能完善设计

## 目标

完善前端下载体验，覆盖三个已确认的缺口：
1. **DXF 分类明细抽屉缺单文件下载** —— 明细表格每行可下载对应 DXF 成品。
2. **大体积下载无法取消** —— 完整备份、分批导出、分类整组/全部、拆板 ZIP、选择导出支持取消。
3. **下载体验问题** —— "无进度像卡死"、重复下载、关闭/离开后仍下载。

## 已确认的关键约束（探索结论）

- 后端已支持干净的中断语义：`settle_stream` 捕获 `GeneratorExit` 将 transfer 标记为 `cancelled/DOWNLOAD_CANCELLED`；`track_export_stream` 将分批导出/完整备份行标记为 `download_failed`，现有前端已有 `download_failed` 的"重新下载"UI。因此**前端只需 Abort 请求，服务端状态自动正确**，不需要改后端取消逻辑。
- 生产流程文件**不能**走 `downloadFile` 单文件签名下载：`require_file_download_access` 对归属 workflow 的文件强制返回 `WORKFLOW_ARCHIVE_DOWNLOAD_REQUIRED`。因此分类明细的单文件下载**必须新增后端端点**，前端不能复用 `downloadFile`。
- "无进度像卡死"根因：`stream_registered_workflow_archive` 先同步把整个 ZIP 写盘再返回响应，期间前端进度条停在 0% "传输中"。

## 范围

- 后端：仅新增一个分类单文件下载端点及其测试；不修改现有下载/流转/审计逻辑。
- 前端：下载基建（`transfer.ts`、`TransferProgressBar`、新增 hook）、5 个下载组件、分类明细单文件下载 UI 及其 e2e 测试。
- 不引入新依赖，不改变 API 路径语义（新增端点除外），不改变上传/转换/任务调度行为。

## 方案

### A. 共享下载基建

**`frontend/src/shared/api/transfer.ts`**
- `downloadBlob` 增加可选参数 `signal?: AbortSignal`，透传给 `apiClient.request`。
- `TransferProgress` 增加 `preparing: boolean`：请求已发出、尚未收到首个下载进度事件时为 `true`；收到进度事件或完成时为 `false`。
  - `initialTransferProgress()` → `preparing: true`
  - `transferProgressFromAxios()` → `preparing: false`
  - `completedTransferProgress()` → `preparing: false`

**`frontend/src/shared/components/TransferProgressBar.tsx`**
- `progress.preparing === true` 时渲染 Spin + "服务器正在生成，请稍候…"，不显示 0% 的进度条。

**`frontend/src/shared/api/useDownload.ts`（新增）**
- 管理 `AbortController` 的 hook，返回 `{ signal, cancel, active, start }`。
- `start()` 中止上一次未完成的请求并创建新 controller。
- `cancel()` 中止当前下载。
- `active` 表示当前是否有进行中的下载。
- 组件 unmount 时自动 abort（修复"离开页面后仍下载"）。
- 独立纯函数导出 `isDownloadCancelled(error)`：识别 axios `CanceledError`/`ERR_CANCELED`，供组件区分"用户取消"与"真实失败"。

### B. 分类明细单文件下载

**后端 `backend/app/modules/workflows/routes/classification.py`（新增端点）**
- `GET /{workflow_id}/dxf-classification/groups/{group_key}/files/{output_name}/download`
- 鉴权：`require_project_member`；读取最新分类 run，无则 404 `CLASSIFICATION_RUN_NOT_FOUND`。
- 定位：`group_key + output_name` 匹配 `run.items`，未命中则 404 `CLASSIFICATION_FILE_NOT_FOUND`。
- 校验：output 文件存在、非 deleted、扩展名为 `.dxf`，否则 409 `CLASSIFICATION_OUTPUT_MISSING`。
- 权限：`require_file_read_access`（项目成员权威，绕过 `WORKFLOW_ARCHIVE_DOWNLOAD_REQUIRED` 的归档限制）。
- 流式：复用 transfer 追踪 + 审计（`operation="dxf_class_single_file"`、`audit_action="dxf_classification_files.download"`）+ `download_headers(stored.original_name)` + `Content-Length`（stat_object），`settle_stream` 包装。参照 `files/routes/downloads.py::download_file` 的单文件流式模式。

**前端 `frontend/src/features/workflows/workflows.api.ts`**
- 新增 `downloadDxfClassificationFile(workflowId, groupKey, outputName, onProgress, signal?)`，经 `downloadBlob` 调用新端点，`fallbackName` 用 `outputName`，错误文案"分类 DXF 下载失败"。

**前端 `frontend/src/features/workflows/DxfClassificationPanel.tsx`**
- 明细抽屉表格新增"下载"操作列：每行按钮，`aria-label="下载 {output_name}"`。
- 点击调用 `downloadDxfClassificationFile`，行按钮显示 loading；下载完成 `message.success`。
- 行内下载与"下载本类/全部"共用同一取消与并发约束（见 C）。

### C. 大体积下载取消 + 体验修复（5 个组件）

应用范围：`WorkflowRetentionControl`（完整备份）、`WorkflowBatchExportControl`（分批导出）、`DxfClassificationPanel`（分类整组/全部）、`DrawingProcessingPanel`/`useNativeWorkflowDownload`（拆板 ZIP）、`DrawingSelectiveExportControl`（选择导出）。

1. **取消按钮**：下载进行中（`active === true`）在进度条旁显示"取消下载"按钮，点击 `cancel()`。
   - 服务端追踪型（完整备份/分批导出/拆板 ZIP）：abort 后服务端将行标记为 `download_failed`，现有"重新下载"UI 自动接管。
   - 无状态型（分类整组/全部、选择导出）：abort 后显示"已取消"提示，重新点击即可重试。
2. **关闭即中断**：Modal 在下载期间可关闭；关闭或组件 unmount 时 abort 进行中下载。`cancel` 引发的错误用 `isDownloadCancelled` 识别，不弹错误提示。
3. **防重复下载**：任一下载进行中，禁用其余下载按钮（同一时间只允许一个下载）。
4. **准备中反馈**：继承 A 的 `preparing` 状态，显示"服务器正在生成"，消除"像卡死"。

## 错误处理

- 用户取消：`isDownloadCancelled(error)` 返回 true 时不展示错误消息，仅提示"已取消"（有状态型由服务端 `download_failed` 状态自然呈现）。
- 真实失败：沿用 `describeApiErrorAsync` → `describeApiError` 现有文案，不改变错误展示契约。
- 端点 404/409：沿用 `describeApiErrorAsync` 解析 JSON 错误信封，文案覆盖 `CLASSIFICATION_FILE_NOT_FOUND`、`CLASSIFICATION_OUTPUT_MISSING`。

## 测试

**后端（pytest）**
- `backend/tests/workflows/test_workflow_dxf_contracts.py` 或同目录新增分类端点测试：
  - 成功：成员下载单个分类 DXF，200、Content-Disposition 正确、transfer/audit 落库。
  - run 不存在 → 404 `CLASSIFICATION_RUN_NOT_FOUND`。
  - group_key + output_name 不匹配 → 404 `CLASSIFICATION_FILE_NOT_FOUND`。
  - output 已删除/非 DXF → 409 `CLASSIFICATION_OUTPUT_MISSING`。
  - 非项目成员 → 403。
  - 中途客户端断开 → transfer 标记 `cancelled`（沿用现有流式契约测试模式）。

**前端（Playwright e2e）**
- `frontend/tests/e2e/workflows/` 或 `frontend/tests/e2e/files/` 新增：
  - 分类明细抽屉单文件下载：mock 新端点，点击行按钮触发下载、断言 `suggestedFilename`。
  - 取消交互：mock 流式端点，下载进行中显示"取消下载"，点击后不再触发浏览器保存框。
- 现有下载相关 e2e（`files-page-buttons.spec.ts`、`workflow-detail.spec.ts`、`workflow-retention.spec.ts`）不回归。

## 验证

- 后端 `make verify-quick` / 相关 pytest 全绿。
- 前端 `npm run typecheck`、`npm run lint`、相关 Playwright 测试通过。
- 手动核对：取消后分批导出/完整备份出现 `download_failed` 与"重新下载"；关闭 Modal 后无残留下载；分类明细单文件下载文件名为 `output_name`。
