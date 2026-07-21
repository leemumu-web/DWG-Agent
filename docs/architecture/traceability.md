# 架构追溯矩阵

本矩阵把 `/home/Creeken/Paper/CAD_research/结构图/` 中的总流程节点映射到当前模块、代码和证据。完整逐文件清单由 [`module-catalog.json`](module-catalog.json) 维护。

## Linux 主流程

| 流程节点 | 模块 | 当前事实 | 主要代码/证据 |
|---|---|---|---|
| `U1-U3` 创建批次、上传、格式检查 | `workflows` + `files` | implemented | `modules/files/{routes/uploads,registration,validation}.py`、`modules/workflows/{routes/intake,intake/registration}.py`、输入 API/服务测试 |
| `U4-U9` 上传完整性、规范化、DWG/Excel 配对 | `workflows` | implemented | `modules/workflows/intake/{registration,conversion,presentation}.py`、`test_workflow_input_*` |
| `U10-U11` 冻结清单、创建 Drawing | `workflows` + `projects` | implemented | `modules/workflows/intake/freeze.py`、projects 公开 Drawing 能力、生产流程测试 |
| 服务器 DWG→DXF、DXF→DWG、DXF 材料表提取 | `cad_processing` | partial | `modules/cad_processing/` 按方向拆分版本策略、批处理、登记和执行；三个独立 Stage 保持原路径，ODA 与真实样本仍需部署验收 |
| `D1-D12` DXF 预处理、分类、分流、报告 | `dxf_classification` | partial | `adapter.py` 固定 Classifier 1.1.0 契约，`persistence.py` 登记两张分类账本和全部输出，`execution.py` 编排 Job/Workflow |
| `E1-E4` Excel Final 处理 | `excel_processing` | partial | `stage_adapter` 隔离 Stage，`execution` 编排 Job/MinIO，`importers`/`persistence` 登记三张 MySQL 关系表；真实 schema、手册库和跨图纸最终屏障仍是依赖/缺口。首份 DXF 材料表因输入域为 DXF，归 `cad_processing/dxf_to_excel` |
| 图纸拆板与设计屏障 | `workflows` | placeholder | 阶段、输入输出、交接 artifact 和 `WORKFLOW_STAGE_NOT_IMPLEMENTED` |
| CAM 工作包 | `workflows` + `windows_execution` | placeholder | 仅阶段与交接契约；没有 CAM 打包算法 |
| `AGENT/RUNNER/ADAPTER/SINOCAM` | `windows_execution` | external | draft control-plane contract；认证、租约、fencing、Runner 未实现 |
| 结果接纳与交付归档 | `workflows` + `operations` | partial | 每日归档可用；SinoCAM 结果接纳与确定性交付清单未实现 |

> 输入规则校正：结构图早期节点写有人工上传 DXF/DWG/Excel；当前已经确认并实现的契约是
> 人工只上传多个 DWG 和一个 Excel，DXF 必须由服务器转换 Job 生成并逐图配对。追溯节点
> 仍沿用 `U1-U11`，但不能据旧文字开放人工 DXF。

## 平台与基础设施

| 架构节点 | 模块 | 当前事实 | 不得误报的目标差距 |
|---|---|---|---|
| `NGINX/API/WEB` | 多模块入口 | implemented | Compose 当前仅 HTTP，没有完成 TLS |
| `MYSQL` | platform + 所有业务模块 | implemented | MySQL 是业务事实源；迁移管理 36 张模型表 |
| `MINIO` | `files` | implemented in Compose | 本地开发可用 local；跨 MySQL/对象不存在单一 ACID 事务 |
| `RABBIT` | `messaging_target` | placeholder | 当前 broker 是 MySQL SQLAlchemy transport |
| `OUTBOX` | `messaging_target` | placeholder | 当前 commit 后投递有补偿，不是事务 Outbox |
| `BEAT/SCHEDULER` | `messaging_target` | placeholder | maintenance 由 API 显式提交，不是周期任务 |
| `Q_* / W_*` | CAD、Excel、operations | partial | worker ready 只证明连接，不证明 Stage/样本可用 |
| `BACKUP/MONITOR` | `operations` | partial | 有手动工具和控制台；没有自动离机备份、指标告警或 RPO/RTO |

## 数据事实归属

- MySQL：身份、项目、文件登记、Job、Workflow、分类、Excel、运维与 Agent 账本。
- Local/MinIO：原始 DWG、服务器生成 DXF、分类分流 DXF、Excel、报告和归档字节。
- Celery broker/result：投递与短期运行数据；不替代 Job、AnalysisResult 或审计。
- Stage：确定性文件处理，不拥有平台身份、项目权限或最终业务状态。
- 前端：提高操作效率、展示结构化反馈和恢复动作，不拥有最终权限与状态机。

## 变更追溯

每个重构提交必须同时满足：运行契约快照不变、module catalog 路径有效、表/operation/task 唯一归属、受影响模块测试通过、文档路径同步。若目标能力仍留白，必须保留端点、schema、错误码和输入输出契约，而不是删除“不好归类”的占位边界。

## 运维脚本追溯

| 稳定入口 | 分类实现 | 主要运行事实 |
|---|---|---|
| `start-all.sh`、`start-dev.sh`、`stop-all.sh`、`status.sh` | `lib/common.sh`、`lib/local_stack.sh`、`lib/database.sh`、`lib/cad_worker.sh` | 本地 MySQL、FastAPI、前端、Nginx 与八组 worker 生命周期。 |
| `db.sh` | `lib/database.sh`、`storage/reap.py` | MySQL schema/种子/迁移/备份与登记对象保留期回收。 |
| `docker.sh` | `lib/compose.sh` | Compose 服务检查、MySQL + MinIO 备份恢复。 |
| `run-cad-worker.sh` | `lib/cad_worker.sh` | CAD 队列、Xvfb、DISPLAY、PID 和优雅退出。 |
| `doctor.sh` | `lib/common.sh`、`windows/forward_to_win11.sh` | 服务版本、HTTP 异常和可选 Windows 反向隧道诊断。 |
| `verify.sh`、Makefile 文档目标 | `docs/check.py`、`docs/generate_api.py`、`architecture/` | 质量门禁、生成 API、路径/契约/归属一致性。 |

`scripts/lib.sh` 只为既有外部 source 调用保留聚合兼容，不是新增实现归属。完整参数和安全边界见 [`scripts/README.md`](../../scripts/README.md)。

## 后端平台追溯

| 运行接口 | 正式实现 | 兼容或装配边界 |
|---|---|---|
| `app.main:app` | `app/bootstrap/application.py` | `main.py` 只重导出 ASGI app。 |
| SQLAlchemy metadata/session/mixin | `app/platform/database/` | `bootstrap/model_registry.py` 显式加载 15 个模型模块和 36 张表；files、jobs 与 workflows 分别通过领域模型包装配其多张表。 |
| 初始角色、权限和管理员 seed | `app/bootstrap/seed.py` | composition 层组合 identity model、platform Session 和 password primitive。 |
| Celery application | `app/platform/messaging/celery_app.py` | `bootstrap/task_registry.py` 显式加载 7 个真实 task module，并注册 jobs stale-recovery 与 control-plane observer；11 个 `app.workers.tasks_*` 公共名不变。 |
| Settings、HTTP envelope/error/dependency、JWT/password、logging | `app/platform/{config,http,security,observability}/` | 业务权限不进入 token primitive；通用 DB dependency 不认识身份或项目。 |
| Local/MinIO 字节接口 | `app/platform/storage/` | adapter、安全路径、选择缓存和健康检查；不导入 ORM 或文件业务。 |

## 后端业务域追溯

| 外部契约 | 正式实现 | 依赖边界 |
|---|---|---|
| `/auth`、`/users`、`/roles`、`/permissions` | `app/modules/identity/` | 其他模块只导入 `identity.interface`；拥有六张 RBAC/token 表。 |
| `/projects`、`/drawings` | `app/modules/projects/` | 其他模块只导入 `projects.interface`；拥有四张项目/图纸表。 |
| `/files` | `app/modules/files/` | 其他模块只导入 `files.interface`；拥有文件、传输和扫描四张表，与 platform byte adapter 解耦。 |
| `/jobs`、`/results`、`/reviews` | `app/modules/jobs/` | 其他模块只导入 `jobs.interface`；拥有 Job/Step/Result/Review 四张表，attempt 状态机与 Celery transport 解耦。 |
| CAD 转换、预览解释与 DXF 材料表 | `app/modules/cad_processing/` | 无自有表和 HTTP 前缀；`files`/`jobs` 只经 `cad_processing.interface` 调用，Stage 代码保持独立产品。 |
| Steel DXF 分类 | `app/modules/dxf_classification/` | 拥有 run/item 两张表；其他模块只经 `dxf_classification.interface` 调用，1.1.0 CLI 和输出命名由 adapter 校验。 |
| `/excel-final` 与 Excel Final task | `app/modules/excel_processing/` | 拥有 batch/part/component 三张表；files/jobs 由公开接口组合，Stage 子进程、导入、持久化和 HTTP route 分层；稳定 task name/queue 不变。 |
| `/workflows` | `app/modules/workflows/` | 拥有 run/stage/artifact/input batch/input item 五张表；模板、状态机、Job 同步、阶段执行、输入四种转换和 16 个 HTTP operation 分层；其他模块只经 `workflows.interface`。 |
| 跨领域 audit write 与 `/audit-logs` read | `app/modules/operations/audit/interface.py` + operations 路由/服务/模型 | 写入统一经 audit interface；读取、ORM、筛选与权限均已归 operations，不再依赖旧横向路径。 |
