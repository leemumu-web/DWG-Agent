# Frontend Network and Loading Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在弱网和动态工作流状态下保留页面数据、明确确认写入结果，并让路由和重型阶段组件形成真实、可量化的按需加载边界。

**Architecture:** 查询使用 AbortSignal、有界抖动重试和重连刷新；写操作通过已完成的 `useAppMutation` 策略清单与 `reliableCommand` 明确区分。路由直接导入页面模块，工作流详情再按阶段懒加载；Vite manifest 测试锁定依赖图和体积，而不是仅检查源代码含 `lazy()`。

**Tech Stack:** React 19、React Router、React Query 5、Axios、Ant Design、Vite 8、TypeScript 6、Playwright。

---

## 文件结构

- Modify: `frontend/src/shared/api/client.ts` — AbortSignal、可靠命令请求配置和 401 重放兼容。
- Create: `frontend/src/shared/api/reliableCommand.ts` — 稳定键、Retry-After 和有界全抖动。
- Modify: `frontend/src/shared/api/error.ts` — retryable 分类和不确定写入恢复提示。
- Modify: `frontend/src/app/providers.tsx` — 查询重连/placeholder 默认值，mutation 全局不重试。
- Modify: `frontend/src/shared/components/ConnectivityBanner.tsx` — offline/recovering/synchronized 状态。
- Modify: `frontend/src/app/router.tsx` — 直接页面模块导入和局部 Suspense。
- Create: `frontend/src/shared/components/RouteFallback.tsx`, `frontend/src/shared/lazy/prefetch.ts`。
- Modify: `frontend/src/features/workflows/WorkflowDetailPage.tsx` — 阶段组件动态边界与下一阶段空闲预取。
- Modify: heavy workflow panel modules — 提供直接动态入口。
- Modify: `frontend/vite.config.ts` — 稳定 vendor/code split 配置，不提高 warning limit。
- Create: `frontend/scripts/check-build-budget.mjs` — manifest 依赖和体积门禁。
- Modify: `frontend/package.json` — `build` 串联预算检查。
- Create/Modify: Playwright weak-network specs and backend frontend-contract tests。

### Task 1: 建立可证明的查询与可靠命令重试策略

**Files:**
- Create: `frontend/src/shared/api/reliableCommand.ts`
- Modify: `frontend/src/shared/api/client.ts`
- Modify: `frontend/src/shared/api/error.ts`
- Modify: `frontend/src/shared/api/index.ts`
- Modify: `frontend/src/app/providers.tsx`
- Test: `backend/tests/contracts/test_frontend_contract.py`

- [ ] **Step 1: 写策略失败测试**

```python
def test_retries_are_bounded_and_mutations_are_opt_in():
    reliable = frontend_source("shared/api/reliableCommand.ts")
    providers = frontend_source("app/providers.tsx")
    assert "MAX_COMMAND_ATTEMPTS = 3" in reliable
    assert "Retry-After" in reliable
    assert "Math.random()" in reliable
    assert "mutations: { retry: false }" in providers
    assert "multipart" in reliable
```

- [ ] **Step 2: 运行并确认模块不存在**

Run: `cd backend && uv run pytest -q tests/contracts/test_frontend_contract.py -k 'bounded and mutation'`

Expected: FAIL。

- [ ] **Step 3: 实现 stable key 和重试函数**

```typescript
const MAX_COMMAND_ATTEMPTS = 3;
const RETRYABLE = new Set([408, 429, 502, 503, 504]);

export async function reliableCommand<T>(key: string, request: () => Promise<T>): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < MAX_COMMAND_ATTEMPTS; attempt += 1) {
    try {
      return await request();
    } catch (error) {
      lastError = error;
      if (!isRetryableCommandError(error) || attempt === MAX_COMMAND_ATTEMPTS - 1) throw error;
      await delay(retryDelayWithJitter(error, attempt));
    }
  }
  throw lastError;
}
```

`reliableCommand` 的调用闭包必须发送同一 `Idempotency-Key`。helper 拒绝 `FormData` 和 Blob body；Retry-After 同时解析秒数和 HTTP-date，最大等待 10 秒。

- [ ] **Step 4: 完成 QueryClient 默认值**

```typescript
defaultOptions: {
  queries: {
    refetchOnWindowFocus: false,
    refetchOnReconnect: true,
    retry: shouldRetryApiQuery,
    retryDelay: queryRetryDelay,
  },
  mutations: { retry: false },
}
```

查询 API 接受 `signal?: AbortSignal` 并传给 Axios；分页组件使用 React Query `placeholderData: keepPreviousData`，不在全局伪造空数据。

- [ ] **Step 5: 运行测试与构建并提交**

Run: `cd backend && uv run pytest -q tests/contracts/test_frontend_contract.py -k 'retry or connectivity'`

Run: `cd frontend && npm run build`

Expected: PASS。

```bash
git add frontend/src/shared/api frontend/src/app/providers.tsx backend/tests/contracts/test_frontend_contract.py
git commit -m "feat: bound frontend network recovery"
```

### Task 2: 完成断网恢复和状态确认反馈

**Files:**
- Modify: `frontend/src/shared/components/ConnectivityBanner.tsx`
- Create: `frontend/src/shared/connectivity/store.ts`
- Modify: production workflow pages using active polling
- Test: `frontend/tests/e2e/workflows/network-recovery.spec.ts`

- [ ] **Step 1: 写 offline/recovering 失败测试**

Playwright 先加载 workflow，切换 `context.setOffline(true)`，断言已有内容仍可见且 banner 为“网络连接已中断”；恢复 online 后 mock 首次同步 503、第二次成功，断言依次显示“正在同步服务器状态”和“已恢复”，最后自动消失。

- [ ] **Step 2: 实现连接状态机**

```typescript
export type ConnectivityState = 'online' | 'offline' | 'recovering';

export function beginReconnect(queryClient: QueryClient) {
  setConnectivity('recovering');
  return queryClient.refetchQueries({ type: 'active', stale: true })
    .then(() => setConnectivity('online'))
    .catch(() => setConnectivity('recovering'));
}
```

恢复事件不清缓存；只有至少一次 active query 同步成功才显示 online。重复 online 事件合并为一个 promise。

- [ ] **Step 3: 写命令不确定状态 UI**

`parseApiError` 对没有响应的 reliable command 返回 `kind='transient'` 和“服务器可能已经受理，正在确认”。调用页面保持 stable key，触发资源/receipt 查询，确认未受理后才允许产生新 key。

- [ ] **Step 4: 运行测试并提交**

Run: `cd frontend && npx playwright test tests/e2e/workflows/network-recovery.spec.ts`

Expected: PASS。

```bash
git add frontend/src/shared frontend/src/features frontend/tests/e2e/workflows/network-recovery.spec.ts
git commit -m "feat: synchronize UI after reconnect"
```

### Task 3: 把路由改为直接页面分包和局部 fallback

**Files:**
- Modify: `frontend/src/app/router.tsx`
- Create: `frontend/src/shared/components/RouteFallback.tsx`
- Modify: page modules only where a default/named dynamic export is required
- Test: `backend/tests/contracts/test_frontend_contract.py`

- [ ] **Step 1: 写直接导入失败测试**

```python
def test_routes_lazy_load_direct_page_modules():
    router = frontend_source("app/router.tsx")
    assert "../features/workflows/WorkflowsPage" in router
    assert "../features/workflows/WorkflowDetailPage" in router
    assert "import('../features/workflows')" not in router
    assert "<Suspense" not in router[router.index("<BrowserRouter>"):router.index("<Routes>")]
```

- [ ] **Step 2: 修改动态入口**

```typescript
const WorkflowsPage = lazy(() => import('../features/workflows/WorkflowsPage').then(named('WorkflowsPage')));
const WorkflowDetailPage = lazy(() => import('../features/workflows/WorkflowDetailPage').then(named('WorkflowDetailPage')));
```

其他 identity、operations、CAD、files 页面按相同直接路径处理。每个 route element 使用 `<Suspense fallback={<RouteFallback label="加载生产项目…" />}>`，AppLayout、导航、ConnectivityBanner 和 error boundary 不随 route chunk 卸载。

- [ ] **Step 3: 处理 chunk 加载失败**

RouteFallback 只显示骨架；局部 error boundary 识别动态 import 错误，提供“重试加载”并重新执行 import，不执行 `window.location.reload()` 作为首选。

- [ ] **Step 4: 运行测试和构建并提交**

Run: `cd backend && uv run pytest -q tests/contracts/test_frontend_contract.py -k 'route or lazy'`

Run: `cd frontend && npm run build`

Expected: PASS，列表与详情产生不同输出 chunk。

```bash
git add frontend/src/app/router.tsx frontend/src/shared/components backend/tests/contracts/test_frontend_contract.py
git commit -m "perf: split direct page routes"
```

### Task 4: 按工作流阶段延迟加载重型面板

**Files:**
- Modify: `frontend/src/features/workflows/WorkflowDetailPage.tsx`
- Create: `frontend/src/features/workflows/stagePanels.ts`
- Modify: heavy panel files only for stable exports
- Create: `frontend/src/shared/lazy/prefetch.ts`
- Test: `frontend/tests/e2e/workflows/workflow-detail.spec.ts`

- [ ] **Step 1: 写未选阶段不加载测试**

Playwright 监听 JS 请求：首次打开 source_intake 时不得请求 Excel Stage2、classification、splitting 和 retention panel chunk；选择分类阶段后只加载 classification；导航和阶段轨道全程可见。

- [ ] **Step 2: 定义阶段 loader**

```typescript
export const stagePanelLoaders = {
  dxf_classification: () => import('./DxfClassificationPanel'),
  drawing_processing: () => import('./DrawingProcessingPanel'),
  excel_stage1: () => import('./ExcelStage1Panel'),
  excel_stage2: () => import('./ExcelStage2Panel'),
  retention: () => import('./WorkflowRetentionControl'),
} as const;
```

WorkflowDetailPage 只为选中阶段创建 lazy component；source_intake 的 ProductionInputPanel 保持当前首阶段直接可用。

- [ ] **Step 3: 实现有界空闲预取**

```typescript
export function prefetchWhenIdle(load: () => Promise<unknown>): () => void {
  if (navigator.connection?.saveData) return () => undefined;
  const id = window.requestIdleCallback?.(() => void load(), { timeout: 2000 });
  if (id !== undefined) return () => window.cancelIdleCallback?.(id);
  const timeout = window.setTimeout(() => void load(), 1200);
  return () => window.clearTimeout(timeout);
}
```

只预取模板中的下一阶段；慢连接或 saveData 不预取。

- [ ] **Step 4: 运行测试并提交**

Run: `cd frontend && npx playwright test tests/e2e/workflows/workflow-detail.spec.ts`

Expected: PASS。

```bash
git add frontend/src/features/workflows frontend/src/shared/lazy frontend/tests/e2e/workflows/workflow-detail.spec.ts
git commit -m "perf: lazy load workflow stage panels"
```

### Task 5: 用 manifest 建立构建预算

**Files:**
- Modify: `frontend/vite.config.ts`
- Create: `frontend/scripts/check-build-budget.mjs`
- Modify: `frontend/package.json`
- Modify: `infra/gateway/nginx/nginx.conf`
- Modify: `infra/gateway/nginx/nginx.local.conf`
- Test: `backend/tests/contracts/test_frontend_contract.py`
- Test: `backend/tests/infrastructure/test_nginx_contract.py`

- [ ] **Step 1: 生成可审计 manifest 基线**

Run: `cd frontend && npx vite build --manifest`

Expected baseline: 最大公共块约 760.65 kB、workflow 块约 119.67 kB，并出现大于 500 kB 警告。把这些数写入执行日志，不提交 `dist/`。

- [ ] **Step 2: 配置稳定分组**

使用 Vite 8 支持的 `build.rolldownOptions.output.codeSplitting.groups` 将 React/React Query、Ant Design 和业务路由分开缓存；如果当前版本不支持该字段，使用 Vite 官方兼容的 `rollupOptions.output.manualChunks`。不得修改 `chunkSizeWarningLimit`。

- [ ] **Step 3: 实现预算脚本**

脚本读取 `dist/.vite/manifest.json` 和资产 stat，断言：WorkflowsPage 与 WorkflowDetailPage 是不同入口；重型 stage panel 只出现在 dynamicImports；任何单个 JS 小于 500000 bytes；生产项目列表初始依赖总字节不超过改造前测得值。

```javascript
if (largest.bytes >= 500_000) {
  throw new Error(`largest JS chunk ${largest.file} is ${largest.bytes} bytes`);
}
```

- [ ] **Step 4: 串联 build 并运行**

`package.json` 使用 `vite build --manifest && node scripts/check-build-budget.mjs`。运行：

Run: `cd frontend && npm run build`

Expected: 无大块警告，预算脚本 PASS。

- [ ] **Step 5: 锁定静态资源缓存边界**

Nginx 对带内容 hash 的 `/assets/` 返回 `Cache-Control: public, max-age=31536000, immutable`，对 `index.html` 返回 `Cache-Control: no-cache`。测试同时验证发布切换不会清空旧 hash 资源所在镜像之前就让旧页面失去 chunk。

Run: `cd backend && uv run pytest -q tests/infrastructure/test_nginx_contract.py`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add frontend/vite.config.ts frontend/scripts/check-build-budget.mjs frontend/package.json infra/gateway/nginx backend/tests/contracts/test_frontend_contract.py backend/tests/infrastructure/test_nginx_contract.py
git commit -m "test: enforce frontend loading budget"
```

### Task 6: 弱网、旧响应和按钮稳定性 E2E

**Files:**
- Create: `frontend/tests/e2e/workflows/network-recovery.spec.ts`
- Modify: workflow/project/file E2E specs
- Modify: `frontend/tests/e2e/contracts/api-contract.spec.ts`

- [ ] **Step 1: 覆盖四类真实故障**

测试：GET 503 后成功；可靠 POST 服务端提交后 abort；两个标签页同键；旧 GET 延迟到新 GET 之后返回。每个场景断言 UI 保留内容、只有一个资源、旧响应不覆盖新版本。

- [ ] **Step 2: 覆盖按钮策略**

枚举所有 `useAppMutation` 的 policy；至少对每类选一个真实 UI 流程验证：reliable command、convergent state、transfer session、download、local only。

- [ ] **Step 3: 运行前端全量**

Run: `cd frontend && npm run build && npx playwright test tests/e2e`

Expected: PASS；条件 skip 逐项记录原因。

- [ ] **Step 4: 提交**

```bash
git add frontend/tests frontend/src
git commit -m "test: cover weak network interaction recovery"
```
