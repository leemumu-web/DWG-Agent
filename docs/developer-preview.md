# v0.1 技术预览指南

## 交付定义

本仓库当前按 **v0.1 技术预览版**交付给技术人员，用于内部试用、接口联调、Stage 集成和后续开发。它已经不是空框架，但也不是生产就绪版本。

| 层级 | 当前结论 |
|---|---|
| 可阅读 | 架构、API、数据库、配置、部署、运维、安全、管线和验证文档齐全并受自动门禁约束 |
| 可构建 | Python/Node 锁文件存在，三个 editable Python Stage 均由父仓库跟踪，前后端本轮构建/测试通过 |
| 可开发 | route/service/task/storage 分层、统一脚本、迁移和测试入口已建立 |
| 可试运行 | 本地 MySQL + Local/MinIO + 五个已实现队列可运行；转换管线仍需显式开关和外部依赖 |
| 非生产就绪 | Compose 仅 HTTP；自动备份恢复、集中监控告警、容量与安全演练未交付；LICENSE 尚未确定 |

明确排除：Agent/model/MCP 执行、CAD 构件提取/分类/拆板/左右进、中望 CAD 二次开发和 Windows CAD Worker。目录或 API 占位不改变这一范围。

## 支持基线

- Linux 开发环境；Python `>=3.12,<3.13`；使用 `uv` 和 `backend/uv.lock`。
- Node/npm；前端锁定 React 19、TypeScript 6、Vite 8，使用 `npm ci`。
- MySQL 8.x 是唯一运行时业务事实源；SQLite 仅用于隔离 pytest。
- Local FS 或 MinIO 保存字节；MySQL 保存元数据、权限和 SHA-256。
- Celery 使用 MySQL SQLAlchemy transport/result backend，不需要 Redis/Valkey。
- DWG↔DXF 需要 ODA File Converter、Xvfb 和适用许可；Excel Final 需要受支持 schema 与 `hardware_handbook` 只读库。

## 首次检出到质量基线

```bash
cp .env.example .env
cp .env.example backend/.env
# 替换全部密码和 JWT secret；两份 MYSQL_* 必须一致。

cd backend
uv sync --locked
cd ../frontend
npm ci
cd ..

make verify-quick
```

不要提交 `.env`、`.env.docker`、虚拟环境、`Data/`、浏览器输出、运行数据库或对象存储。`Stages/dxf2excel/original_dxf/` 只保留 `.gitkeep`；历史 419 文件 corpus 需从许可合规的外部位置获取。

## 初始化与启动

```bash
bash scripts/db.sh setup-user
bash scripts/db.sh init
bash scripts/start-dev.sh
```

入口：

- Vite：`http://127.0.0.1:5173`；
- FastAPI：`http://127.0.0.1:8010`；
- 本地 Nginx 构建版：运行 `bash scripts/start-all.sh` 后访问 `http://127.0.0.1:8080`。

`start-dev.sh` 启动五个已实现队列：`report`、`dxf`、`dxf2dwg`、`dxf2excel`、`excel_final`。worker 存活不代表关闭的功能开关或外部依赖已经满足。

## 开发者首日检查

```bash
bash scripts/status.sh
bash scripts/doctor.sh --since-minutes 60
make docs-check
cd backend && uv run alembic current && cd ..
```

预期先确认：MySQL 配置一致、迁移处于 `f7a9c2d4e610`、OpenAPI 为 104 paths / 124 operations、生产配置关闭运行时 API 文档、Compose 未发布 443。

## 修改路径

| 需求 | 主要修改处 | 必要验证 |
|---|---|---|
| 新增/修改 API | `backend/app/schemas`、`services`、`api/v1` | service/route/权限负例、`make docs-generate`、`make docs-check` |
| 修改 Job/worker | `services/job_service.py`、`workers/` | status + attempt、重试、旧执行、取消、对象补偿测试 |
| 修改存储 | `backend/app/storage/`、file transfer service | Local/MinIO contract、SHA-256、rollback compensation、一致性扫描 |
| 修改数据库 | model + 新 Alembic revision | SQLite 回归、`alembic check`、空 MySQL migration-test |
| 修改前端 | `frontend/src/api/`、`features/` | `npm run build`、相关 Playwright、真实 API 权限复核 |
| 修改 Stage | 对应 `Stages/*` | Stage 单测、平台 adapter/task 测试、许可合规真实样本 |
| 修改文档 | 对应中文文档 | `make docs-check` |

route 只处理 HTTP/dependency；service 负责事务、不变量与授权范围；Celery task 调用 service。列表必须先在 SQL 中过滤权限再分页。worker 所有领取、进度和终态写入都匹配 status + attempt。

## 验证层级

```bash
# 日常提交前
make verify-quick

# 发布候选；外部环境缺失时可记录 blocked，但代码门禁仍必须通过
bash scripts/verify.sh full --allow-blocked
```

2026-07-18 本轮结果：后端 937 passed / 6 skipped；DWG→DXF 30 passed；DXF→DWG 30 passed；Excel Final 259 passed；Playwright 87 passed / 6 skipped；基础设施 82/82；文档、Ruff、Alembic 和 Compose 通过。DXF→Excel 内置测试在纳入父仓库后单独复验。隔离 MySQL 空库迁移因非交互会话没有 sudo 凭据而 blocked，不能视为通过。详见[审计报告](audit-report-2026-07-18.md)。

## 发布前必须解决

1. 在临时 clean checkout 重放锁定安装、全部门禁和前后端镜像构建。
2. 公网入口实现 TLS、HTTPS 跳转、Secure cookie、证书续期和真实握手验证。
3. 实现协调的 MySQL + MinIO 备份、恢复演练、保留、RPO/RTO 证据。
4. 增加集中日志、指标、告警、容量测试和事故责任边界。
5. 确认 ODA、Stage、第三方依赖和样本数据许可；由负责人选择仓库 LICENSE。
6. 使用有效业务 corpus 完成真实 MySQL/Celery/MinIO/Nginx 的上传、处理、重试、SSE、下载、中断恢复闭环。

在这些条件完成前，版本说明必须继续使用“内部技术预览”，不得写成生产可用、开源发行或全格式兼容。
