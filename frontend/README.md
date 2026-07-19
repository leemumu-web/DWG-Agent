# 前端

前端使用 React 19、TypeScript 6、Vite 8、Ant Design 和 React Query，覆盖认证、仪表盘、项目、用户/角色/审计、文件、四条转换页面、Job、复核、生产流程、预览、重试、下载和管理员基础设施概览。

```bash
npm ci
npm run dev      # http://127.0.0.1:5173
npm run build
```

经本地 Nginx `http://127.0.0.1:8080` 时保持 `VITE_API_BASE_URL` 为空；Vite 直连 FastAPI 时使用 `http://127.0.0.1:8010`。access token 位于 `sessionStorage`，refresh/SSE token 使用 HttpOnly cookie。UI 权限与按钮隐藏只是交互辅助，FastAPI 才是授权边界。

Axios 对并发 401 合并执行一次 refresh；React Query 对普通 query 默认重试两次。单文件下载最多尝试两次，每次先获取新的 300 秒签名；只重试网络、403、408、429 和 5xx。ZIP 使用认证 POST stream，不共享该重签名逻辑。

```bash
npm run build
npx playwright test
```

Playwright 默认通过 Nginx `8080`。真实 Excel Final 摘要闭环需要 `PLAYWRIGHT_EXCEL_SAMPLE_PATH` 指向业务有效样本；route fixture 只能证明 UI/API contract，不能证明 MySQL/Celery/MinIO 实际链路。生产流程 `source_intake` 复用 `/files` 上传多个 DWG 和单个 Excel，服务器创建 DWG→DXF Job、显示逐文件配对并在冻结时创建图纸；后续核心留白阶段仍只暴露接口和交接产物契约。详见[验证文档](../docs/workflow-verification.md)。
