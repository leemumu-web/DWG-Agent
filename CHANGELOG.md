# 变更记录

本文件记录技术预览版之后影响用户、部署或开发契约的变化。历史提交细节以 Git 记录为准。

## [Unreleased]

### Added

- `steel-dxf-split` 新增 `--lean-report` 开关：`report.json` 精简为验收所需字段，不生成 PNG 预览，`weld_allowance_report.json` 照常生成；BH 与 BOX 均生效。后端拆板调用默认启用精简报告。
- 按架构、参考、指南、验证四层重建项目文档，并建立领域化仓库重构设计与可回滚实施计划。
- 增加运行契约快照、12 模块归属清单和结构图追溯矩阵；校正 Celery 稳定任务数为 11。
- 恢复非破坏性的运行栈验证脚本，保持统一门禁的静态检查入口完整。
- 面向技术人员的 v0.1 技术预览指南、全量审计报告和贡献指南。
- 文档门禁动态校验根 README OpenAPI 数、数据库参考中的 Alembic head/表数和 DXF→Excel Stage 跟踪边界。
- `make verify-quick`、`make verify-full` 与 full gate 的 DXF→Excel Stage 测试。
- Linux 十阶段 `linux_production` 模板、模板能力 API、文件/结果产物绑定和统一阶段执行端点。
- 生产流程控制台的文件筛选/绑定、DXF→Excel、Excel Final、占位契约和产物下载操作。
- 本机工作站部署适配说明：见 `docs/guides/local-workstation-deploy.md`。

### Changed

- Job、JobStep、AnalysisResult 与 ReviewRecord 四张事实表归入 `app.modules.jobs`；创建/attempt 状态机、Celery 投递、SSE 当前状态、stub 执行、恢复和复核按职责拆分，跨领域调用统一经过 `jobs.interface`。
- 694 行 Job API 拆为查询、命令、事件、结果和复核 routes，保持 13 个 Job、4 个 Result、1 个 Review operation 不变；platform messaging 通过通用 worker-ready callback 装配 stale Job 恢复，不再导入 Job ORM。
- 文件登记、四张流转/扫描事实表、项目范围权限、上传登记、签名/ZIP 导出与 MySQL/Local/MinIO 补偿统一归入 `app.modules.files`；其他业务模块只通过 `files.interface` 使用。
- 1482 行文件 API 按上传、目录、批次、预览和下载拆分；存储选择/健康属于 `platform.storage.factory`，文件校验、登记、导出、生命周期与补偿分文件维护。
- 身份/RBAC 与项目/图纸目录由横向 `api/models/schemas/services` 归入 `app.modules.identity` 和 `app.modules.projects`；跨领域调用统一经过各自 `interface.py`，HTTP 与数据库契约保持不变。
- `api/deps.py` 拆为 platform DB dependency、identity authentication/global-role dependency 与 projects membership policy；审计写入形成 operations 稳定 interface。
- 应用 router 和依赖领域模型的幂等 seed 归入 `app.bootstrap`，共享时间戳 mixin 归入 `app.platform.database`，消除 platform 对业务模块的反向依赖。
- 后端公共技术能力按 config、database、http、messaging、observability、security、storage 归入 `app.platform`，应用/模型/任务装配归入 `app.bootstrap`；`app.main:app` 保持稳定门面。
- Celery 官方运行入口迁至 `app.platform.messaging.celery_app:celery_app`，11 个已发布 `app.workers.*` 任务名保持不变，确保已排队消息兼容。
- Alembic、Compose、脚本、测试和文档统一使用显式模型/任务 registry；架构测试禁止平台层反向依赖业务模块并拒绝旧平台包导入。
- 运维脚本改为稳定 facade + 分类实现：数据库、Compose、本地进程和 CAD worker 生命周期分别归入 `scripts/lib/`，CAD、Windows、storage、docs 工具归入对应目录。
- `scripts/db.sh`、`docker.sh`、启动/停止/状态/诊断/验证命令保持调用方式；`scripts/lib.sh` 降为旧调用者兼容聚合。
- 基础设施按 gateway、database、storage、messaging、operations、verification 分类；Windows 目标边界拆为 Node Agent、CAM Runner、SinoCAM Adapter 与协议。
- Compose、Nginx、本地脚本、文档和测试同步新路径；RabbitMQ/Outbox/Beat 保持真实目标留白。
- 删除与 `frontend/public/logo.png` 字节相同的根 `image.png`，运行日志无损迁入网关目录。
- `Stages/dxf2excel` 从不可还原 gitlink 转为父仓库普通跟踪源码；外部验证 corpus、生成工作簿、PDF、cache 和虚拟环境继续排除。
- 当前文档事实更新为 OpenAPI 114 paths / 135 operations、Alembic `e2f4b8c6a130`、36 张模型表和完整 runtime 最多 45 张表。
- 工作流直接复用现有 Job/Celery 与 `/files`：自动阶段按工作流/阶段幂等绑定 attempt，成功结果自动挂接，取消流程同步取消 active Job。
- 自动阶段失败或被单独取消后可从同一 executions 端点重试：复用 Job、递增 attempt、重开原阶段并保持旧 worker fencing；显式取消流程仍不可重开。
- Linux 生产阶段按模板强制校验 artifact type，任意文件类型不能绕过占位/外部交接条件。
- `.dockerignore` 排除本机工作数据目录（`tmp_data/`、`太子/`、拆板前 DXF 汇总、BH/BOX 优化等），避免构建上下文膨胀到 20 GB 以上。
- `scripts/lib/common.sh` 引入 `DOCKER_BIN`/`dck`：在普通用户无 docker-socket 访问权、需 sudo 的宿主机上透明代理 docker 命令；无需 sudo 的环境行为不变。`docker.sh`/`status.sh`/`start-all.sh`/`verify.sh` 同步改用统一入口。
- 清理旧发布环境：移除本机 gitignore 的 `releases/`（r36→r39.5 加密发布包，约 7.6 GB）与旧 SQLite 开发库（`var/app.db`、`backend/var/app.db`、`var/backups/`）。本机部署改为直接源码构建，发布包按需经 `scripts/release.sh` 生成。
- 文档体系补齐：`docs/README.md` 操作指南新增[本机工作站部署适配](docs/guides/local-workstation-deploy.md)；根 README 与 `docs/guides/deployment.md` 增加本机 sudo-docker / 非 80 端口差异说明。
- `dwg2dxf` 引擎强制 `APPIMAGE_EXTRACT_AND_RUN=1`（与生产容器一致）：宿主机缺 `libfuse2` 时 ODA AppImage 的 FUSE 挂载会在清理时断连（ENOTCONN），即使 DXF 产物已写出也被判失败；提取模式不依赖 FUSE。

### Fixed

- Job 静态路径统一在 `/{job_id}` 之前装配，并以精确 method/path/function-name 契约防止后续新增静态入口被参数路由遮蔽。
- 修正 `/files/bulk-delete` 和 `/files/download-zip*` 注册在 `/{file_id}` 之后而可能被参数路由遮蔽的问题，新增 17 个 method/path/function-name 顺序契约。
- 修正 Celery 任务开始/结束信号向控制面观测函数传递错误关键字的问题，并增加任务 ID 转发回归测试。
- BOX 拆板元数据解析支持无“数量”列的料表模板（`零件编号/规格/长度/材质/重量`）：有唯一“长度”表头时按长度表头 X 坐标最近邻消歧名义长度，不改变原“长度+数量”模板行为。
- BOX 拆板内轮廓让位同圆心 Bolt 圆孔：当 Part 图层的 ARC+切线环与 Bolt 图层 CIRCLE 指向同一物理孔时，内轮廓不再作为独立内孔（避免材料被重复挖掉后圆孔校验失败）。该去重只在环的所有端点落在既有 Bolt 圆孔圆周上时触发，不影响其他孔与既有数据。

### Known limitations

- Compose 仅发布 HTTP，不提供 TLS。
- 图纸拆板、CAM 工作包、Windows Node Agent/SinoCAM 和结果接纳只有接口与产物契约，核心执行尚未实现。
- 自动备份恢复、集中监控告警和生产容量验收尚未交付。
- 仓库 LICENSE、ODA/第三方组件和样本数据分发策略尚待负责人确认。

## [0.1.0-preview] - 2026-07-18

- 建立 FastAPI、React、MySQL、Celery SQL transport、Local/MinIO 的内部技术预览基线。
- 提供身份/RBAC、项目/文件、任务/结果/复核/审计、双向 CAD 转换、DXF→Excel、Excel Final、数据控制台和人工工作流骨架。
- 建立 attempt-safe Job 状态、存储流转/一致性处置、中文文档和分层自动验证。
