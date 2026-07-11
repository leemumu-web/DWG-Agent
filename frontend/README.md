# Frontend / 前端

## English

React 19 + TypeScript 6 + Vite 8 operational UI for authentication, dashboard, projects, users/roles/audit, files, four conversion workflows, Jobs, reviews, previews, retries, and downloads.

```bash
npm ci
npm run dev      # http://127.0.0.1:5173
npm run build
```

Keep `VITE_API_BASE_URL` empty behind Nginx (`http://127.0.0.1:8080`). Set it to `http://127.0.0.1:8010` for direct Vite -> FastAPI development. Access state uses `sessionStorage`; refresh/SSE tokens are HttpOnly cookies.

Axios performs one coalesced 401 refresh. React Query retries ordinary queries twice. Single-file downloads have a separate maximum of two attempts and obtain a new 300-second signature each time. ZIP downloads use authenticated POST streaming and do not share that re-sign loop.

UI permission guards are navigation aids only; FastAPI is the security boundary. Agent and Windows CAD pages are not delivered features.

### Browser Tests

```bash
npm run build
npx playwright test
```

Default browser/API base is Nginx `http://127.0.0.1:8080`. Direct debugging may set `PLAYWRIGHT_FRONTEND_BASE_URL` and `PLAYWRIGHT_API_BASE_URL`. A successful Excel Final digest path needs a business-valid sample via `PLAYWRIGHT_EXCEL_SAMPLE_PATH`; an arbitrary spreadsheet is intentionally invalid.

Mocked route tests prove frontend contracts, not a real MySQL/Celery/MinIO workflow.

## 中文

基于 React 19 + TypeScript 6 + Vite 8 的操作界面，覆盖认证、dashboard、项目、用户/角色/审计、文件、四条转换工作流、Job、复核、预览、重试和下载。

```bash
npm ci
npm run dev      # http://127.0.0.1:5173
npm run build
```

经 Nginx（`http://127.0.0.1:8080`）时保持 `VITE_API_BASE_URL` 为空。Vite -> FastAPI 直连开发时设为 `http://127.0.0.1:8010`。access 状态使用 `sessionStorage`；refresh/SSE token 是 HttpOnly cookie。

Axios 对 401 执行一次合并 refresh。React Query 对普通 query 重试两次。单文件下载有独立的最多两次尝试，并且每次获取新的 300 秒签名。ZIP 下载使用认证 POST stream，不共享重签名循环。

UI permission guard 只是导航辅助；FastAPI 才是安全边界。Agent 和 Windows CAD 页面不是已交付功能。

### 浏览器测试

```bash
npm run build
npx playwright test
```

默认 browser/API base 是 Nginx `http://127.0.0.1:8080`。直连调试可以设置 `PLAYWRIGHT_FRONTEND_BASE_URL` 和 `PLAYWRIGHT_API_BASE_URL`。Excel Final 成功摘要路径需要通过 `PLAYWRIGHT_EXCEL_SAMPLE_PATH` 提供业务有效样本；任意 spreadsheet 是有意负例。

Mocked route 测试只证明前端 contract，不证明真实 MySQL/Celery/MinIO 工作流。
