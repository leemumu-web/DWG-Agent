# 前端

前端使用 React 19、TypeScript 6、Vite 8、Ant Design 和 React Query，覆盖认证、仪表盘、项目、用户/角色/审计、文件、四条转换页面、Job、复核、生产流程、预览、重试、下载和管理员基础设施概览。

```bash
npm ci
npm run dev      # http://127.0.0.1:5173
npm run check:architecture
npm run build
```

## 源码边界

```text
src/
├── app/                 # Router、Provider、应用壳层
├── shared/              # api、auth、通用组件；不得依赖 feature
└── features/
    ├── identity/        # 登录、个人资料、用户与角色
    ├── projects/        # 项目、成员、图纸目录
    ├── files/           # 文件登记、上传、预览与下载
    ├── jobs/            # Job、Step、Result 与事件
    ├── workflows/       # 生产流程、输入冻结、分类阶段
    ├── cad-processing/  # DWG/DXF 转换与 DXF→Excel
    ├── excel-processing/# Excel Final 导入、查询与预览
    ├── operations/      # 审计、存储、归档与控制平面
    └── ...              # dashboard、reviews、automation
```

API、类型、领域 hook 和页面随其 feature 放置；跨 feature 只允许导入目标目录的
`index.ts`。`app` 也只通过这些公共入口装配路由。顶层 `api/components/hooks/stores/types/utils`
属于已退役结构，架构检查器会拒绝其重建、shared 反向依赖或私有跨域导入。

大型页面进一步按职责细分：`cad-processing/components/conversion` 保存通用转换的上传、
文件夹、进度和表格模型，`components/dxf2excel` 保存 DXF 批次上传与动作卡片；
`excel-processing/model` 保存 Excel 预览解析模型；`operations/components/data-console`
保存六类运维面板。源码文件不得超过 600 行，防止页面重新退化成单体。

经本地 Nginx `http://127.0.0.1:8080` 时保持 `VITE_API_BASE_URL` 为空；Vite 直连 FastAPI 时使用 `http://127.0.0.1:8010`。access token 位于 `sessionStorage`，refresh/SSE token 使用 HttpOnly cookie。UI 权限与按钮隐藏只是交互辅助，FastAPI 才是授权边界。

Axios 对并发 401 合并执行一次 refresh；React Query 对普通 query 默认重试两次。单文件下载最多尝试两次，每次先获取新的 300 秒签名；只重试网络、403、408、429 和 5xx。ZIP 使用认证 POST stream，不共享该重签名逻辑。

```bash
npm run build
npx playwright test
```

Playwright 规范位于 `tests/e2e/{contracts,excel-processing,files,jobs,operations,workflows}`，
共享测试环境只放在 `tests/e2e/support`。可以使用 `npm run test:e2e:files`、
`npm run test:e2e:operations` 等目录脚本聚焦回归。上述每个源码、测试和共享分区都带有
本地 `README.md`，说明职责、依赖、输出与能力边界。

Playwright 默认通过 Nginx `8080`。真实 Excel Final 摘要闭环需要 `PLAYWRIGHT_EXCEL_SAMPLE_PATH` 指向业务有效样本；route fixture 只能证明 UI/API contract，不能证明 MySQL/Celery/MinIO 实际链路。生产流程页以“新建生产项目”为主入口，原子创建并启动 workflow 后进入独立详情页。`source_intake` 通过 `/input-excel` 上传一个 `.xls`/`.xlsx`，通过 `/input-dwg-folder` 上传 DWG 文件夹；混合文件夹确认后只发送 DWG。服务器创建 DWG→DXF Job、显示逐文件配对并在冻结时创建图纸；冻结后进入 DXF 分类控制台，显示 Steel DXF Classifier 1.1.0 的 Job 进度、类型汇总、逐图分流/诊断和 JSON/CSV/DXF 下载。后续拆板等核心留白阶段仍只暴露接口和交接产物契约。详见[验证证据](../docs/verification/current.md)。
