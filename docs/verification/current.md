# 全栈工作流验证

## 2026-07-24 Linux 生产图纸 DXF 规范流验收

本轮将 `linux_production` 固定为一个 Excel 加多份 DWG 的输入合同。源 DWG 只在
`source_intake` 入库；每份 DWG 转换并验证为 `canonical_dxf` 后，DrawingVersion、分类、
图纸处理、CAM、接纳和交付阶段的图纸主格式全部为 DXF。revision 3 迁移对无法证明格式正确
的历史 artifact 失败关闭，不静默改名。

| 门禁 | 结果 | 当前证据 |
|---|---|---|
| 真实 HTTP/Celery/MySQL 链路 | **pass** | 工作流 `5`、项目 `11` 上传 1 个 XLSX 和 2 个真实 DWG；转换 Job `1650/1651` 生成 File `1112/1113`，冻结清单 SHA-256 为 `41ebd94501fecddfaba0d00ff26d15411cac50c4798ccaabb569403846235dd4`。 |
| DXF 分类 | **pass with review** | Job `1652` attempt `3`、run `3` 完成；2 个输入均为 DXF，2 个输出因 `TITLE_FIELD_MISSING` 明确进入 `review_required`，JSON 报告 File `1117`、CSV 清单 File `1118` 已登记。 |
| 数据库格式不变量 | **pass** | 2 个 DrawingVersion 均为 `source=workflow_input_dxf` 且引用 `.dxf`；除 `source_intake/source_dwg` 外的 DWG artifact 数为 0；分类输出与各自 canonical DXF 的 SHA-256 完全一致。 |
| 长路径与事务回归 | **pass** | 分类 transfer request ID 使用 63 字符稳定摘要，不再把中文相对路径直接写入 64 字符列；生成文件沿用显式持久 transfer 边界，避免 MySQL error 1020。 |
| Backend 全量 | **1365 passed，10 skipped** | 工作流格式/必需产物门禁、迁移失败关闭、分类持久化和全部既有回归通过。 |
| Frontend 全量 | **118 passed，1 skipped** | 工作流 DXF 主格式提示、九阶段产物合同及全部浏览器回归通过。 |
| 独立 Stage | **30 + 30 + 17 + 52 + 239 passed，24 skipped** | DWG→DXF、DXF→DWG、DXF→Excel、Steel DXF Classifier 和 Excel Final 通过。 |
| 迁移/基础设施 | **pass** | 空 MySQL schema 升级至唯一 head `c7b2d4e9f601`，42 张业务表和种子验证后清理；活动基础设施 **122 / 122**。 |
| 静态/架构/文档/构建 | **pass** | Ruff、141 个分区文档、42 表/167 operation/13 task 运行时快照、文档生成检查、前端 production build 和 `git diff --check` 均通过。 |

真实样本只覆盖本次两份板零件图。分类器没有从缺失标题栏中猜测零件类型，后续需复核状态属于
正确业务结果；尚未实现的图纸处理、CAM、Windows 和结果接纳仍保持 placeholder/external，
本次只收紧其输入输出格式和必需产物门禁。

## 2026-07-24 Excel Final 与生产流程接入发布证据

本轮把 Excel Final 收敛为独立工作中心，并作为 `linux_production` 冻结输入后的
`excel_stage1` 真实执行阶段；DXF→Excel 继续保留在文件转换区，不进入主生产流程。
工作流列表只负责创建和查找流程，`/workflows/:workflowId` 独立承载输入冻结、九阶段状态、
当前操作、错误说明与产物下载。无真实实现的阶段不会提交虚假任务。

| 门禁 | 结果 | 当前证据 |
|---|---|---|
| 统一 full 门禁 | **pass** | `PASS=17 FAIL=0 BLOCKED=0`；包含静态检查、架构/文档、后端、Alembic、基础设施、Compose、空库迁移、五组 Stage、生产构建和浏览器回归。 |
| Backend 全量 | **1300 passed，9 skipped** | Excel 结果文件事务、结构化输入错误、手册/公式查询、工作流冻结 Excel 执行和 Job 并发取消回归通过；22 条依赖弃用类 warning，无失败。 |
| 基础设施 | **122 / 122** | 当前 15 个 Compose service、活动 MySQL 51 张运行表、环境键、Nginx、文件和死代码边界通过。 |
| 隔离 MySQL 迁移 | **pass** | 临时空 schema 升级至唯一 head `8a6c1f4e2b90`，验证 42 张业务表和管理员种子后自动清理。 |
| 独立 Stage | **30 + 30 + 17 + 52 + 239 passed，24 skipped** | 统一门禁覆盖 DWG→DXF、DXF→DWG、DXF→Excel、Steel DXF Classifier 和唯一 Excel Final；另从 backend 生产依赖环境执行 Excel Final 全量为 **263 passed**，包含真实手册 MySQL 用例。 |
| Playwright 全量 | **120 passed，2 skipped** | 122 个浏览器场景无失败；真实大 XLS 默认关闭，DXF→DWG 暂停在无活动任务时按条件跳过。 |
| 真实 Excel E2E | **pass** | 首体院预处理原表实际完成上传、预检、Celery 处理、MySQL 登记、签名刷新和下载；Job `1177`、Batch `9`、结果 File `838`，四步均成功。 |
| 真实结果统计 | **pass** | 527 个零件、46 个构件；总净重 `122013.557 kg`、总毛重 `124831.881 kg`；质量 `ok`，0 警告。 |
| 最终工作簿 | **pass** | 188628 bytes，SHA-256 `213e6a1f1cc587efb0dc21b89d093161810cdeec9ab3267d4eed85dc14fe8097`；六张目标表齐全，整理表 3515 个公式、part 122 个公式。 |
| 最终展示边界 | **pass** | part 共 122 条，类型仅 `BOX腹` 42、`BOX翼` 42、空 38；J 备注与 K 文件全部为空，报告为“无”，整理表/part/报告无隐藏行列。 |
| 手册唯一标准 | **pass** | 活动 `hardware_handbook` 审计为 1967 条语义记录、2025 条源行、0 问题；重复重量返回 `conflict`，不任取一条。 |
| 查询规则 | **pass** | 板材固定 7.85；D 系列按材质路由圆钢/螺纹钢，Q235B 圆钢规则统一；PIP/PD 使用圆形空心截面公式；螺栓、螺套、TT 留空，其余查无明确提示。 |
| 错误闭环 | **pass** | 无法识别的文本伪 XLS 在创建 Job 前返回结构化 422；页面只展示受控错误码、具体原因和操作建议，不产生僵尸 Job。 |
| 并发取消 | **pass** | MySQL 1020 竞争已通过取消前 `FOR UPDATE` 行锁消除；真实 DWG 上传/暂停连续三轮 6/6 通过，API 日志无 1020、500 或 Traceback。 |

真实样本验证只证明上述首体院输入及当前运行环境；未覆盖的同类 Tekla 版式仍通过结构化错误
进入人工修正，而不是猜测列含义。公式计算痕迹保留在最终 Excel，数据库继续保存追溯和质量
信息，但最终操作表不展示已明确删除的内部辅助列。

## 2026-07-22 文档分类、后端平台层与业务域迁移发布基线

本节是当前重构的权威回归基线；后续带日期的小节保留历史证据，不覆盖本节结果。

| 门禁 | 结果 | 当前证据 |
|---|---|---|
| 文档一致性 | pass | 分类文档集合、相对链接、生成 API、端口、数据库 head/表数与生产文档开关通过 |
| 文档契约聚焦测试 | pass | `4 passed, 1 warning` |
| 后端全量 | pass | `1093 passed, 6 skipped, 21 warnings in 122.76s`；在既有回归上新增预留队列 route 与分区源码说明完整性测试，没有删除或放宽旧测试 |
| OpenAPI | pass | 114 个 path、135 个 operation；生成文件为 `docs/reference/api.md` |
| ORM / Alembic | pass | 15 个模型模块、36 张模型表；17 个线性 revision；单一 head `e2f4b8c6a130`；`alembic check` 无漂移 |
| Celery 公共任务与路由 | pass | 11 个 `app.workers.*` 稳定任务名和 10 条 `pattern -> queue` 路由保持不变；7 个真实 task module 显式装配，Agent/CAD/dispatch 只有预留路由且没有 task；官方运行入口为 `app.platform.messaging.celery_app:celery_app` |
| 架构契约 | pass | 运行时快照与 12 模块目录通过；36 表、135 operation、11 task 唯一归属，10 条任务路由由快照直接比对 |
| 架构聚焦测试 | pass | `72 passed, 6 warnings`；除平台/领域依赖、public interface、registry、退役路径和 module catalog 外，新增 10 条 task route 与每个直接源码均被 README 说明的静态契约 |
| Identity/projects 聚焦回归 | pass | `264 passed, 13 warnings`；认证、RBAC、token、项目/图纸服务、分页、审计、dependency 与安全边界通过 |
| Files 聚焦回归 | pass | `158 passed, 7 warnings`；上传、登记、传输账本、补偿、预览、下载、存储一致性与架构边界通过 |
| Jobs 聚焦回归 | pass | `140 passed, 3 skipped, 15 warnings`；创建、批量创建、attempt 隔离、取消/重试、投递补偿、SSE、Result/Review 权限、stale 恢复与架构边界通过 |
| CAD / 分类聚焦回归 | pass | `98 passed, 7 warnings`；三个转换方向、批处理、DXF 预览、Classifier 1.1、稳定任务名/队列、两张分类表、跨域接口与退役路径通过 |
| Excel 处理聚焦回归 | pass | `40 passed, 1 skipped, 1 warning`；Stage adapter/真实 runner 启动、流式导入、三张模型表、attempt 清理/重试、请求幂等、14 个 route 与领域边界通过；跳过项需要真实 MySQL |
| Workflow 聚焦回归 | pass | `73 passed, 1 warning`；五张表、16 个 route、十阶段能力、多个 DWG + 单 Excel、服务器派生 DXF、冻结/删除保护、Job 同步、分类账本和留白契约通过 |
| Operations/automation 聚焦回归 | pass | `160 passed, 3 skipped`；归档、数据目录、存储对账/处置、基础设施、控制平面、Agent memory、禁用契约、任务恢复和跨域边界通过 |
| 前端领域边界 | pass | 106 个 TypeScript 源文件归入 app、shared 与 11 个 feature；超大页面拆为 CAD 上传/文件夹/总览/列模型、DXF→Excel 卡片、Excel/Workflow 展示模型和六个运维面板；单文件上限 600 行；29 项前端源码契约与 production build 通过 |
| E2E 分区 | pass | 9 个 spec 归入 contracts、excel-processing、files、jobs、operations、workflows，support 单独保存环境；Playwright 成功收集 98 个用例 |
| Playwright 全量 | pass | 最终统一门禁 `93 passed, 5 skipped in 2.4m`；同一最终源码另两轮为 94/4、95/3，跳过数随活动 Job/批次前置条件变化，三轮均无失败 |
| 分区说明 | pass | backend、tests、infra、scripts、frontend/E2E 自动发现源码 owner，并显式覆盖 Stage、Agent 与 Windows 产品边界，共 134 个维护分区；均有就地业务 README，架构检查拒绝缺失、空泛、未引用真实源码或未声明能力边界的文档 |
| 独立 Stage 回归 | pass | `30 + 30 + 17 + 52 + 239 passed，24 skipped`；三个 CAD Stage、Classifier 1.1.0 与唯一 Excel Final 流程通过 |
| 统一 quick 门禁 | pass | Shell、ruff、架构、221 项聚焦后端、文档、前端 production build 共 6 gate 全部通过 |
| 统一 full 门禁 | pass | `PASS=17 FAIL=0 BLOCKED=0`；包含空 MySQL 迁移、五组 Stage、全量后端、基础设施、Compose、文档、构建和 Playwright |
| 基础设施分类 | pass | gateway/database/storage/messaging/operations/verification 与 Windows 四边界均有路径测试 |
| 基础设施验证 | pass | `122 / 122`；Nginx、13 个 Compose service、挂载、环境键、活动 MySQL 45 表、种子、权限与时间列全部通过 |
| 隔离 MySQL 迁移 | pass | 临时 schema 从空库升级全部 17 个 revision 至 `e2f4b8c6a130`，验证 36 张业务表与管理员种子后自动清理 |
| 基础设施聚焦回归 | pass | `104 passed, 7 warnings`；Compose config、架构快照、文档门禁同时通过 |
| 脚本分层聚焦回归 | pass | `132 passed, 2 skipped`；稳定 facade、递归 Shell 语法、数据库/Compose/存储/Windows 通信与文档路径通过 |
| 脚本真实入口 | pass | `db.sh check=0`、`docker.sh check=0`；MySQL 45 表、应用凭据、Compose 与 MinIO 配置均由新分层入口验证 |

自动化基线证明隔离后端、Stage、浏览器合同、文档和架构契约；下述本机运行检查另行证明当前 MySQL/Celery/FastAPI 实例可用，两者不能互相替代。尚未使用获准生产 DWG 对 ODA 输出质量做本轮发布验收，也没有实现 RabbitMQ、Outbox、Beat、Windows Node Agent、CAM Runner 或 SinoCAM Adapter。当前 Celery 明确使用 MySQL SQLAlchemy transport。人工初始盘点曾漏掉 `classify_steel_dxf`，机器 registry 确认稳定任务总数为 11，现已同步设计、计划和文档。

### 与 `delete` 参考节点的无缺失复核

本轮以提交 `4d93ed5cb60b86585f9dbdba9f3e9bc57c6e90bd`（`delete` 完成后的仓库）为参考节点，使用独立 detached worktree 导入两套应用并比较运行时和源码合同，而不是依据文件名猜测迁移是否完整。

| 对比面 | 参考节点 | 当前结果 | 结论 |
|---|---:|---:|---|
| FastAPI | 114 path / 135 operation | 114 / 135 | method、path、operationId 零缺失 |
| SQLAlchemy | 36 表 | 36 表 | 表、字段类型、nullable、主键、外键、唯一约束和索引零差异 |
| Alembic / Settings | 单一同 head / 同环境字段 | 同参考节点 | revision 集合与配置键无缺失 |
| Celery | 11 task / 10 route | 11 / 10 | 三条预留 route 曾在重构中遗漏，本轮恢复；仍无 Agent/CAD/dispatch task |
| React Router | 18 URL | 18 URL | URL 集合零缺失 |
| 前端公共导出 | 266 名称 | 309 名称 | 参考导出全部存在，新增 43 个分区组件/类型出口 |
| 后端公共定义 | 471 名称 | 567 名称 | 参考符号全部存在；增强的文件删除接口增加 DB 参数以执行冻结输入保护 |
| Compose | 13 service | 13 service | 服务、队列、依赖和健康语义保留，只有分类后的源码/挂载路径变化 |
| Pytest 收集 | 1000 | 1099 | 参考用例仅有四个旧模块形态节点被更严格的 interface/capability 测试替代，没有对应行为空洞；当前结果为 1093 passed、6 skipped |

参考节点的六个旧脚本实现路径已按既定重构方案退役，真实实现位于 `scripts/cad/`、`scripts/docs/`、`scripts/storage/` 和 `scripts/windows/`；回归测试明确禁止旧路径复活。稳定的人机入口仍是根层 Shell facade。参考节点的一行 Agent/MCP/ZWCAD 空模块没有执行行为，当前以 capability contract、HTTP 503 和就地 README 表达同一留白，避免用可导入空文件制造“已实现”错觉。

2026-07-22 最终运行刷新使用官方入口启动八组 worker：report、dxf-classification、dxf、dxf2dwg、dxf2excel、excel-final、dispatch、maintenance；进程命令均加载 `app.platform.messaging.celery_app:celery_app`，旧 `app.workers.celery_app` 进程已退出。`scripts/status.sh` 确认本机 MySQL 应用凭据、45 张运行表、管理员种子与时间列，FastAPI `:8010` 运行源码时间一致，前端 production dist 为最新。历史 root Nginx 日志/pid 已逐一改名保留为 `*.root-before-20260722-release`；本地配置改用仓库自有 `logs/client-body/` 上传缓冲并去除启停 sudo 依赖后，Nginx `:8080`、SPA、`/health`、`/health/ready` 全部可达，readiness 同时报告 database/storage `ok`。该修复来自真实 Playwright 首轮 3 个上传 500，精准重跑 3/3、随后全量 94 passed/4 skipped。根 `image.png` 与 `frontend/public/logo.png` SHA-256 完全相同，已删除前者并由 README 复用后者；历史 Nginx runtime logs 仍位于忽略的部署日志目录。

脚本接口现分为三层：仓库根 `scripts/*.sh` 保持既有操作命令；`scripts/lib/` 分别拥有通用、数据库、Compose、本地栈和 CAD worker 生命周期；CAD 基准、Windows 转发、存储维护、文档生成/检查进入对应分类目录。`scripts/lib.sh` 仅保留兼容聚合，新增脚本必须按需依赖具体库。旧 Python/Windows 实现路径已退出，Makefile、测试和文档均指向分类路径。

后端公共技术能力现归入 `backend/app/platform/` 的 config、database、http、messaging、observability、security、storage 七个子边界；应用装配归入 `backend/app/bootstrap/`，`app.main:app` 只保留稳定 ASGI 门面。模型与任务由显式 registry 装配，平台层通过 AST 契约禁止反向依赖业务模块；旧 `core/`、`db/`、`storage/`、`utils/` 生产导入已退出。基础设施验证器同时检查 CAD worker 门面委托与分类实现，避免目录重组后出现“只验证门面、不验证实际命令”的盲区。

identity 与 projects 已成为首批完整业务切片：routes、models、schemas、应用服务分别在领域目录内分组，其他业务代码只能通过 `interface.py` 使用身份、全局角色、项目成员和图纸目录能力。六张身份表和四张项目/图纸表的 owner 由架构测试锁定；旧 `api/deps.py` 及对应 route/model/schema/service 文件均已删除。通用 DB dependency 与 timestamp mixin 留在 platform，HTTP router 和依赖 identity model 的 seed 留在 bootstrap，审计写入通过 operations audit interface，platform→modules 反向依赖保持为零。

files 现按“登记事实、存储适配、跨系统事务”三层拆分：领域模块独占 `files`、`file_transfers`、`storage_scan_runs`、`storage_scan_findings` 四张表，`platform/storage/factory.py` 只负责后端选择、缓存、健康检查和本地路径解析。上传、目录、批次、预览、下载五类 routes 保持原有 17 个 method/path/function-name 契约，并强制所有静态路径先于 `/{file_id}` 注册，修复旧实现中批量删除和 ZIP 下载路径可能被参数路由遮蔽的问题。其他业务模块只通过 `app.modules.files.interface` 使用文件能力；旧横向 file model/schema/service/API 路径已退出。

jobs 现按创建、attempt 生命周期、Celery 投递、事件流、恢复、复核和 HTTP 用例分层，领域模块独占 `jobs`、`job_steps`、`analysis_results`、`review_records` 四张表。13 个 Job、4 个 Result、1 个 Review operation 的 method/path/function-name 保持不变，静态 Job 路径先于 `/{job_id}` 组合；其他业务域只通过 `app.modules.jobs.interface` 调用。平台消息层只保留通用 Celery app、SQL transport 与 worker-ready callback，Job stale 恢复由 bootstrap 注册，platform 不再反向导入业务模型。当前仍是 commit 后直接投递并对明确 broker 错误补偿，不是 transactional Outbox；SSE 仍读取 `jobs.progress_data` 最新快照，没有持久事件编号和 replay；这些目标差距均保留在模块与架构文档中。

cad_processing 现把共享 attempt/source/JobStep 原语、ODA 目录批调用、DXF 统计、纯预览渲染和预览缓存登记分别隔离；DWG→DXF 与 DXF→DWG 各自拥有 contracts、版本策略、产物登记、单任务和批任务文件，DXF→材料表拥有批次 staging、产物登记和执行文件。dxf_classification 则把 Classifier 1.1 CLI/schema/退出码/命名契约放入 adapter，把冻结来源、分流文件、run/item 和 AnalysisResult 账本放入 persistence，把 Job/Workflow 顺序留在 execution。Files 仍拥有文件行和传输 saga，Jobs 仍拥有任务事实；外部业务模块只经两个 `interface.py` 调用。图纸自动拆板仍是下一阶段明确留白，不因分类器目录完善而改变实现状态。

excel_processing 现把一份 904 行 route 和 826 行 service 拆为 processing/catalog/tools/health HTTP 入口、访问与幂等规则、files 上传 saga 复用、源文件 staging、流式 workbook importer、批次持久化、响应投影、Stage adapter/runner、Job execution 与稳定 Celery task。14 个 method/path/function-name、三张表、`excel_final` queue 和历史公共 task name 均未改变；所有静态 route 先于参数 route。jobs 的取消和 stale 恢复不再直接删除 Excel 模型，而通过 `excel_processing.interface` 请求域内级联清理。该结构只证明既有单文件 Excel Final 切片，跨全部图纸的最终屏障、左右进合并、自动汇总和外部手册数据仍是明确缺口。

workflows 现把两个 route、两个 model、两个 schema 和两个 service 横向文件收拢到领域目录：
五张表按编排/输入分组，模板、生命周期、artifact、Job attempt 同步和阶段执行计划分别归档，
输入按登记、转换、冻结和面向操作员的诊断展示拆分，16 个 operation 按原顺序组合。files
删除保护只通过 workflow 公开接口请求冻结引用，Classifier 也不再调用 workflow 私有函数。
人工输入仍为多个 DWG + 一个 Excel，DXF 只由服务器生成；四个后续核心阶段保持
placeholder/external。完整后端、Alembic、架构/文档门禁和前端 production build 均通过；
最终刷新后的 FastAPI、八组 worker 与 Nginx 当前可达，但仍未把隔离测试描述为真实 ODA/SinoCAM 生产验收。

operations 现按 audit、daily archive、data catalog、storage reconciliation 和 control plane
五个 owner 分层；automation 把三张已交付数据表/会话记忆/API 与未实现的
Agent/MCP/ZWCAD/Windows 执行契约分开。归档的签名冻结预检、流式 ZIP/manifest、MinIO 与
files/transfer 双登记不变；扫描 run/finding 表仍由 files 域拥有，处置仍要求签名 token、
actor 绑定、幂等键、目标快照、数量/字节上限和永久删除确认词。旧
`app/api/models/schemas/services/workers` 横向业务源码及一行占位 adapter 已退出。

任务 registry 从包含三个空占位的 8 个 module 收敛为 7 个真实 module；11 个历史公共任务
名、10 条任务路由和 report/maintenance 等队列保持不变。`agent`、`cad`、`dispatch` 仅保留
确定路由与队列契约，未注册任务。platform 通过通用 worker-signal callback 通知由 bootstrap 注册的 control-plane
observer，不反向导入业务模块；两个方向的导入顺序和 worker-ready 顺序均有回归覆盖。

前端现由 `app`、`shared` 与 11 个 feature 纵向边界组成。认证刷新合并、sessionStorage、
HttpOnly cookie SSE、上传并发、Job attempt、下载重签名、生产批次连续提交、DXF 分类、
Excel Final 桥接和每日归档交互均保留原实现；API、类型、页面和领域组件只改变归属路径。
架构脚本拒绝旧顶层横向目录、shared 反向依赖、跨 feature 私有导入和超过 600 行的源码，
并已接入 `npm run build`。样式已从单一 `src/styles.css` 拆到 shared 与六个 feature；
E2E 也按 7 个工作区归档。134 个维护边界由真实源码 owner 自动发现并补充 Stage/Agent/Windows 产品边界，统一检查本地 README，不以总览文档或固定手工清单替代。

> **范围：** Nginx、FastAPI、MySQL、Celery SQL transport、storage、frontend retry/SSE/download
> **最近发布验证：** 2026-07-25

## 2026-07-25 DXF 分类 1.2 发布证据

本轮把 DXF 分类从固定类型汇总升级为可供后续阶段读取的逐图分类账本：明确覆盖 PX 等工程
类型，安全自动发现新英文前缀，数据库保存原始/规范规格、类型来源、稳定分组键和下一阶段
可用标记。工作流页面按分类文件夹展示和分页查看逐图信息，仅对待确认/无法读取预警；分类
页面不展示 JSON/CSV，可下载任一分类或全部分类的 DXF-only ZIP。

| 门禁 | 结果 | 当前证据 |
|---|---|---|
| Classifier 1.2 Stage / 独立仓库 | pass | 两处源码字节一致；各自 `108 passed`，独立仓库 compileall 与 1.2.0 sdist/wheel 构建通过 |
| 后端全量 | pass | `1393 passed, 10 skipped, 22 warnings in 168.69s` |
| 分类/Workflow 聚焦回归 | pass | 数据库语义、分组详情、下一阶段输入、真实 ZIP 成员/传输账本和前端合同共 `222 passed, 1 warning` |
| 前端 production build | pass | 122 个 TypeScript 源文件、12 个 feature boundary，Vite production build 通过 |
| Playwright 全量 | pass | 最终复跑 `117 passed, 3 skipped in 3.1m`；分类文件夹、详情、预警、分类/全量下载和生产输入闭环通过 |
| Alembic / 活动 MySQL | pass | 单一 head `d6f3a8c2e710`；活动 schema 已在 head；空临时 MySQL schema 完整升级 28 个 revision，验证 42 张业务表与种子后自动清理 |
| 基础设施 | pass | `95 / 95`；Compose 15 services、MySQL、Nginx、环境与路径检查通过 |
| 实际运行进程 | pass | FastAPI 与 dxf-classification worker 已安全重启；`:8010` 和 Nginx `:8080` 均返回健康/就绪 200、150 个 OpenAPI path，三个新分类路由可见，运行分类器报告 1.2.0 |

真实 ZIP 语料的既有 243 张 BH/BOX/PL 回归分布继续保留，但其中没有 PX。PX、动态新类型和
不确定类型的本轮自动化样本通过，不能替代未来获准 PX 生产图纸的人工质量验收。自动拆板、
Windows CAM 和 SinoCAM 仍是后续阶段，不因分类目录和下载闭环完成而改变实现状态。

## 1. 证据层级

| 层级 | 能证明 | 不能证明 |
|---|---|---|
| Static/docs | source/config/link/schema 声明内部一致 | 进程启动或依赖可用 |
| SQLite/backend tests | 隔离 API/service/security/state 逻辑 | MySQL lock、migration、broker、MinIO、browser 行为 |
| Stage tests | 确定性 converter/parser unit 和 parity corpus | 每个真实 CAD/workbook 或平台集成 |
| MySQL/infra checks | 空 schema migration 与活动本地 schema/config 事实 | 完整 Job/object/browser 工作流 |
| Playwright contract/UI | API 可达和浏览器交互；部分测试使用 route fixture | 每个场景都使用真实 Celery/MinIO/有效业务文件 |
| Live E2E | 本次运行实际覆盖的部署路径和样本 | 未来 revision 或未测格式/中断 |

验收声明必须写明层级、环境、日期、样本和跳过项。

## 2. 必要生产形态路径

```text
Browser -> Nginx HTTP :8080 本地 / :80 Compose
  -> FastAPI :8010 本地 / :8010 internal
     -> MySQL 业务 + Celery runtime state
     -> Local FS 或 MinIO object
Celery worker <- MySQL queue -> Stage -> MySQL state + storage result
```

没有 Redis/Valkey。当前 Compose 只有 HTTP；HTTPS 不属于该已验证路径。`Stages/dxf2excel` 源码已纳入父仓库，但其 419 文件历史 corpus 不随仓库分发；本轮只重放内置 Stage 单测。

## 3. 可重复门禁

```bash
make docs-check

cd backend
uv run ruff check app tests ../tests/run_full_verify.py ../scripts/docs/check.py ../scripts/docs/generate_api.py
uv run pytest -q
uv run alembic check
cd ..

cd Stages/dwg2dxf && uv run pytest -q
cd ../dxf2dwg && uv run pytest -q
cd ../dxf2excel && uv run pytest -q
cd ../excel_final && uv run pytest -q
cd ../..

bash scripts/db.sh migration-test
bash infra/verification/verify.sh
docker compose config --quiet

cd frontend
npm run build
npx playwright test
```

本地 stack 已运行时，通过 Nginx 使用只读 verifier：

```bash
DWG_VERIFY_USERNAME=admin \
DWG_VERIFY_PASSWORD='<configured-password>' \
python tests/run_full_verify.py
```

生成 OpenAPI 当前包含 114 个 path、135 个 operation。只读 verifier 检查 liveness、readiness、login、精确分页 files/Jobs read 和受管 process topology；它不创建处理 Job/工作流、不上传文件、不中断存储，也不验证签名 result digest。

## 3.1 2026-07-19 生产输入冻结发布证据

本轮把 `source_intake` 收敛为“人工上传多个 DWG + 恰好一个 Excel，服务器生成 DXF 并冻结”的专用闭环。文件字节仍由 `/files` 管理，DWG→DXF 复用既有 Job/Celery/ODA 路径，冻结创建 Drawing/Version 和规范清单哈希；人工 DXF、第二个 Excel、摘要/格式异常、同名冲突、通用 completion 绕过和冻结文件旁路删除均被拒绝。

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| 输入 service/API/CAD batch 聚焦回归 | **29 passed** | 登记、真实对象校验、转换幂等/attempt 重试、broker 失败补偿、配对、冻结、项目权限、OpenAPI schema、冻结文件删除保护。 |
| Backend 全量 | **988 passed，6 skipped** | 当前全部 API/service/security/state/migration 回归；15 条既有 dependency/deprecation warning。 |
| Frontend contract | **24 passed** | 页面级生产批次提交、创建后自动启动并在同一抽屉原地进入上传、draft 原地恢复入口、专用面板、DWG/Excel accept、人工 DXF 错误、UUID 上传幂等键、冻结确认与 API 路径。 |
| Frontend production build | **pass** | React 19 + TypeScript 6 + Vite 8；服务器返回的 `source_dwg`/`source_excel` 角色与 UI 一致。 |
| Playwright 生产输入场景 | **1 passed** | 在 Nginx 当前构建上验证拒绝人工 DXF、上传 DWG/Excel、服务器配对反馈、冻结确认和只读清单；route fixture 不写真实生产数据。 |
| API/文档一致性 | **pass** | 105 paths / 125 operations；生产输入和分类查询响应使用具名 Pydantic envelope，不是空 OpenAPI schema。 |
| 活动 MySQL 与全栈 | **pass** | 活动库已增量升级到 `a9e4c7d2f610`，当前 41 张运行表；FastAPI 源码时间一致，六类 worker、Nginx、API proxy 和 SPA 全部正常。 |
| 独立代码复核 | **pass** | 并发创建、单 Excel 行锁、broker 补偿和冻结文件保护四项 Important 修复后复核，无剩余 Critical/Important。 |

本轮没有向真实项目提交业务 DWG，因此 Playwright 证明 UI/API 状态契约，980 项隔离测试证明服务器不变量，运行状态证明当前 MySQL/worker/API/Nginx 拓扑可用；真实 ODA 输出质量仍须在发布批次中用获准 DWG 样本验收，不能用 fixture 冒充。

## 3.2 2026-07-19 Linux 生产工作流证据

本轮以当前源码搭建 `linux_production` 十阶段服务器框架。DXF 分类分流、DXF→Excel 与 Excel Final 调用既有 Job/Celery 接口；图纸拆板、CAM 工作包、Windows CAM 和结果接纳只暴露稳定输入、产物和 501 留白契约，不把核心算法伪装为已实现。

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| 十阶段贯通测试 | **pass** | 输入冻结、DXF 分类分流、留白交接、DXF→Excel Job/Result、设计屏障、Excel Final Job/Result、CAM 三段交接与交付归档。 |
| 失败恢复 | **pass** | 自动阶段失败/单独取消后停留原阶段；同一 executions 请求复用 Job、attempt +1、清除错误并重新投递；显式取消流程仍保持终态。 |
| Backend 全量 | **959 passed，6 skipped** | API/service/security/state、旧工作流兼容与新增生产工作流回归；15 条既有 dependency/deprecation warning。 |
| Stage 测试 | **30 + 30 + 17 passed** | 当时的 DWG→DXF、DXF→DWG 与 DXF→Excel 证据；已退役 Excel 兼容套件不再列入当前门禁。 |
| Frontend build | **pass** | TypeScript 6 + Vite 8 生产构建；工作流模板、文件绑定、真实执行、留白探测、任务/产物显示。 |
| Playwright 全量 | **91 passed，2 skipped** | 真实本地 API 的 93 个浏览器场景；文件页用例按需建立独立源文件夹具，消除批量删除后的状态泄漏。 |
| Infrastructure / Compose | **82/82 pass** | Nginx、worker wrapper、环境键和 Compose 结构；`docker compose config --quiet` 通过。 |
| 空 schema migration | **pass** | 独立 `dwg_agent_migration_test_976010` 从空 schema 升级到 `d5e8a1c4b720`，29 张表、管理员种子 1 条，随后删除测试 schema。 |

上述十阶段贯通是隔离数据库中的服务器状态机与真实 Job/Result 模型集成证据；它不代表留白算法或 Windows/SinoCAM 已经实现，也不替代带有效 CAD/Excel 样本的 Celery/对象存储发布验收。

## 3.3 2026-07-19 DXF 分类分流发布证据

分类阶段严格读取冻结批次的服务器派生 DXF，暂存时规范化为 `*_拆板前.dxf`，调用 `steel_dxf_classifier.cli --json`，再把逐图分流 DXF、JSON 报告和 CSV 清单分别登记到 MySQL 与对象存储。下一阶段 `drawing_processing` 仍是明确留白，不会由分类完成自动越过。

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| Classifier 1.1.0 自测 | **52 passed** | 文件名、读取、分类、分流目录、JSON/CSV 契约和 CLI 退出码。 |
| 真实 DXF CLI 样本 | **pass** | 原工程 DXF 以 `验证项目_dxf` 输入，生成 `验证项目_BH_dxf/*_拆板前.dxf`、分类报告 JSON 与分类清单 CSV，退出码 0。 |
| 平台全量回归 | **988 passed，6 skipped** | 分类 API、Job attempt、MinIO/File/AnalysisResult/workflow artifact、run/item 台账、迁移及旧功能回归。 |
| 浏览器流程 | **1 passed** | 同一抽屉完成创建、DWG/Excel 上传、服务器 DXF、冻结、启动分类、状态反馈和结果下载。 |
| 活动数据库/进程 | **pass** | `a9e4c7d2f610` head、41 张运行表、`dxf_classification` 独立 worker，Nginx/FastAPI/SPA 健康。 |
| 生产 MinIO | **pass** | `docker compose --profile workers config` 包含分类 worker；MinIO healthy，并完成临时对象 put/stat/get/字节核对/remove 闭环，未改动业务对象。 |

本次没有向用户真实生产项目注入验证记录；真实 CLI 使用仓库外保留的获准验证样本，平台存储与台账由隔离集成测试和活动 MySQL schema/worker 验证共同覆盖。

## 4. 2026-07-18 CAD 转换控制台验证证据

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| Backend 全量回归 | **924 passed，6 skipped** | 多文件夹原子删除、回滚、权限、活动 Job 取消、批量提交契约与既有 API/service/security/state。 |
| Stage 测试 | **30 + 30 passed** | 当时的 DWG→DXF 与 DXF→DWG 证据；已退役 Excel 兼容套件不再列入当前门禁。 |
| Frontend build | pass | TypeScript 6 + Vite 8 生产构建。 |
| Playwright 全量 | **82 passed，3 skipped** | 双向转换提交/重试、可信进度、加载态、多文件夹打包/原子删除/失败保留选择，以及既有真实 API 交互。 |
| Infrastructure / Compose | **82/82 pass** | Nginx 语法、CAD worker 包装脚本队列/App/PID 就绪契约、环境键与 Compose 结构。 |
| 活动 MySQL 只读检查 | pass | 应用凭据、37 张表、super_admin 种子、TimestampMixin 列与无 SQLite 文件句柄。 |
| 临时空库迁移演练 | blocked | 非交互会话无 `sudo` 凭据，未能创建临时 MySQL schema；`alembic check` 已通过，但不冒充空库演练。 |

真实 Nginx `:8080` 页面检查了桌面和 390 px 窄屏。DWG→DXF 显示成功 826/1335、失败 37、待提交/重试 509、成功进度 62%；DXF→DWG 显示成功 954/968、失败 13、待提交/重试 14、成功进度 99%。两页最终控制台无错误。在真实数据上只打开了两文件夹的删除确认，验证其显示 197 个已知源文件、生成结果、活动任务和事务提示后取消，没有删除生产数据。

## 5. 必要端到端场景

| 场景 | 必要证据 |
|---|---|
| Clean checkout/build | fresh clone 恢复全部 Stage source；锁定 backend/frontend install 和 image build 通过 |
| Cold Compose | 空 MySQL/MinIO volume 到达 migration head，core/selected worker healthy |
| Authentication | Nginx login、access request、cookie refresh、logout/revocation 和 expired session |
| Job dispatch | API 创建 queued attempt；MySQL broker 投递给预期 worker；JobStep 和 terminal state 持久化 |
| Object closure | source/result `files` row 与 stored object、download SHA-256 一致 |
| Retry isolation | failed/cancelled Job 递增 attempt；旧 message/worker 不能更新 |
| SSE | HttpOnly cookie 生效、当前 attempt snapshot 到达、reconnect 刷新、terminal 关闭 |
| Authorization | 拒绝跨项目和无项目 result/file/review 越权 |
| Download retry | 首次 signed fetch 以可重试状态失败；第二次获得不同且有效签名 |
| Storage outage | liveness 保持 200；readiness 503；恢复无需 API restart；旧对象保持完整 |
| Worker loss | stale running Job 变为 `CELERY_WORKER_LOST`，随后 retry 完成新 attempt |
| TLS | 真实 HTTPS handshake、redirect、Secure refresh/SSE cookie、signed download 和证书生命周期 |

TLS 和 clean-checkout 两行当前是已知失败，不是已完成验收项。

## 6. 2026-07-12 数据控制台与存储事务证据

本轮首先在本地 MySQL/local storage 上执行只读全量扫描，再用独立 Compose project、独立空 MySQL/MinIO 卷和新后端镜像执行可变更 E2E；没有对本地真实异常执行清理。

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| Backend 全量回归 | **841 passed，5 skipped** | SQLite 隔离 API/service/security/state；15 条 dependency/deprecation warning，无失败 |
| Backend Ruff | pass | `app` 与 `tests`；新增账本、扫描、处置、分页和 Celery 冷启动代码 |
| 聚焦 backend 回归 | **190 passed，2 skipped** | 流转模型/service、Local/MinIO adapter contract、上传/生成/ZIP/下载、扫描/四类处置、data-admin API、迁移、连接池和前端 source contract |
| Documentation checker | pass | 中文文档集、生成 API、链接、端口、数据库表数/head、仓库边界和生产文档行为 |
| Alembic 模型漂移 | pass | `alembic check` 无新增 upgrade operation；保留已知 drawings/version 环依赖 warning |
| 空库迁移脚本 | pass | 临时 MySQL schema 从零升级至 `6d2f8a9c1b40`，验证 28 张业务表、种子数据并清理临时库 |
| Infrastructure / Compose 静态门禁 | **110/110 pass** | Nginx、11 个 Compose service、Dockerfile、活动 MySQL 37 张完整运行时表、环境与文件契约；`docker compose config --quiet` 通过 |
| Frontend build | pass | TypeScript 6 + Vite 8；数据控制台、任务、审计和转换页服务端分页 |
| Playwright 全量 | **69 passed，1 skipped** | 真实本地 API：认证/API contract、上传、失败重试、流式 ZIP、签名下载、双向转换页、Jobs 和数据控制台；指定真实 XLS 样本不存在的成功链路按设计跳过 |
| Playwright 数据控制台 | **2 passed** | 最终源码与真实本地 API：五页签、文件/对象/中文流水详情、中文状态筛选、open finding、处置空选择提示、无 console error；Jobs/Audit 首次只请求 `page=1&page_size=20` |
| 本地只读扫描 | pass | scan #1：548 条登记、12,778 个对象、2 个缺失、12,232 个未登记、0 个大小不符、79 个软删除保留；未执行真实处置 |
| 空卷 Compose | pass | MySQL/MinIO/API/report worker healthy；迁移到 `6d2f8a9c1b40`，首次 Kombu 表/索引无 metadata-lock 自锁 |
| MySQL + MinIO 可变更 E2E | pass | 3 次真实上传入库；1 次签名下载出库且 SHA-256 一致；扫描得到 1 missing、2 untracked、1 retained；四种处置全部执行并将 4 个 finding 标记 resolved |

空卷首次验证先暴露了旧实现的真实缺陷：Kombu `queue_declare` 后 session 未提交，随后 `CREATE INDEX ix_kombu_message_queue_timestamp_id_visible` 等待同一 worker 连接持有的 metadata lock，worker 永远没有 ready marker。修复后显式 commit/close channel session，再执行索引；从全新卷重建后 report worker 正常 healthy 并消费扫描任务。

全量浏览器复验又发现 Excel 便捷上传复用了认证依赖已经开启的 MySQL `REPEATABLE READ` Session，却在独立事务中新建/推进流水，再回旧快照执行 `SELECT ... FOR UPDATE`，稳定触发 MySQL 1020。现在 Excel `/upload` 与 `/upload-and-process` 先在当前事务登记意图并提交，再写对象和业务元数据；失败仍由补偿监听器删除对象并结算流水。ZIP 浏览器用例同时移除了“列表第一条一定已经拥有双格式结果”的历史数据假设，改为明确请求必然存在的源格式；后端仍严格拒绝不完整 ZIP，没有放宽一致性规则。

前端最终截图位于 `output/playwright/data-console-final.png`。截图来自最后一次通过的浏览器回归，状态、动作和风险提示均已中文化，处置动作在未选中同类 finding 时保持禁用并明确提示。隔离 Compose 前端镜像构建曾因 Docker Hub `node:22-alpine` IPv6 metadata 请求超时失败；后端新镜像正常构建，真实 API/MySQL/MinIO/Celery 验证不依赖该失败步骤，源码前端由本地 `npm run build` 和 Vite + Playwright 验证。

## 7. 2026-07-13 PR #1 选择性吸收与数据监视证据

本轮没有直接合并冲突 PR，也没有修改其远程分支。实现从当前 `main` 延伸，吸收字段扩容、流程桥接和前端工具思路，重写了 DXF 预览、权限聚合、事务登记和前端状态管理。真实运行探针使用独立上传文件，没有处置既有扫描异常。

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| PR 审查 | pass | PR #1 共 6 个提交、22 个文件，基于旧主干且冲突；拒绝双 Alembic head、回改历史迁移、Celery/Kombu DDL、旧 8000 端口、失配 npm/uv lock、PNG 慢渲染和前端 N+1 |
| Backend Ruff | pass | `app`、`tests` 与全量 verifier；DXF SVG、缓存/流水、Excel Final 聚合/分页/迁移和 Compose 契约 |
| Backend 全量 | **866 passed，5 skipped** | SQLite 隔离 API/service/security/state；15 条既有 dependency/deprecation warning，无失败 |
| Documentation checker | pass | 91 个 OpenAPI path / 110 个 operation、中文文档、端口、28 张模型表和当前 Alembic head |
| Alembic | pass | 单一 head `9c4e7b1a2d60`；`alembic check` 无待生成 operation；空 MySQL schema 完整升级并验证 28 张业务表/种子数据 |
| Infrastructure / Compose | **110/110 pass** | 活动 MySQL 37 张运行时表；生产 Compose、workers profile、开发覆盖及 workers profile 均可合并 |
| Stage tests | **28 + 28 passed** | 当时的 dwg2dxf、dxf2dwg 证据；已退役 Excel 兼容套件不再列入当前门禁 |
| Frontend install/build | pass | `npm ci`、TypeScript 6、Vite 8；DXF 预览与 Excel Final 控制台 production bundle |
| Playwright 全量 | **72 passed，1 skipped** | 真实本地 API；新增鉴权 DXF Blob、Excel Final 精确总览/工具/详情、失败重试和 DXF→Excel 桥接；未提供外部真实 XLS 样本路径的条件用例按设计跳过 |
| DXF 性能对照 | pass | 同一约 5 MiB / 21,117 文档实体样本：PR Matplotlib PNG 约 27.28 秒；当前 ezdxf SVG recording 约 1.724 秒，输出约 2.62 MiB |
| Local + MySQL 探针 | pass | 文件 #891 首次生成、二次缓存命中；SVG 1,573,087 bytes；`preview_generate` 与 outbound `preview` 均 succeeded 且实际字节一致 |
| MinIO + MySQL 探针 | pass | 当前源码临时 API 连接健康 Compose MinIO 与真实 MySQL；真实 3.26 MiB DXF #894 生成 SVG #895，二次缓存命中；MinIO object listing 显示 registered=true，生成/出库均 succeeded 且 1,888,900 bytes |
| UI 规范与截图 | pass | 最新 Web Interface Guidelines 自审；输入标签/名称、图标按钮、focus-visible、reduced-motion、服务端分页、控制台弃用警告清理；截图 `output/playwright/excel-final-data-console-final.png` |

真实 MySQL 曾在第一次浏览器预览中暴露 `REPEATABLE READ` 快照问题：来源行锁事务先开始，独立 storage session 后创建流水，旧快照再 `SELECT ... FOR UPDATE` 新流水时触发 MySQL 1020。当前实现先在调用者事务持久化 `preview_generate` 意图并提交，再渲染/推进独立状态，最后在来源锁定事务中写对象登记和完成流水；失败由独立结算保留 `failed`，对象写后业务回滚仍触发补偿删除。

MinIO 探针没有重建或替换运行 33 小时的 Compose 容器。它启动当前源码的临时 8011 API，连接现有健康 MinIO 容器和真实 MySQL，完成探针后立即停止；因此证明的是当前代码对真实 MinIO/MySQL 的登记和读取闭环，不是旧 Compose 镜像已经部署本次提交。

## 8. 2026-07-13 生产一致性二轮硬化证据

本轮在第 6 节的 PR 选择性吸收基础上，继续收紧 Excel Final 请求幂等、查询域、DXF 预览生命周期和前端可恢复监视状态。验证使用现有真实 MySQL、local storage 和运行中的 Compose MinIO；探针只删除自身创建的对象和合成 Job，没有处置既有业务文件或一致性 finding。

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| Backend Ruff | pass | `app`、`tests`、全量 verifier、迁移/文档生成脚本 |
| Backend 全量 | **879 passed，5 skipped** | SQLite 单元/集成回归加真实 MySQL 并发幂等用例；15 条既有 dependency/deprecation warning，无失败 |
| MySQL 并发重放 | **连续 5 次 pass** | 两个独立 Session 同键竞争只产生一个 Job；失败者在 savepoint rollback 后以 locking current read 越过旧 consistent snapshot 读取胜者 |
| Alembic | pass | 单一 head `d5e8a1c4b720`；活动库增量升级、`alembic check` 无待生成 operation；空 MySQL 完成 13 个 revision，验证 28 张业务表、唯一约束与种子数据 |
| Local + MySQL 事务探针 | pass | Excel file #903 / Job #1080 重放未复制对象；DXF #904 / SVG #905，677 bytes；上传、预览生成、鉴权出库、源删除失效和软删除流水均 succeeded |
| MinIO + MySQL 事务探针 | pass | 使用 Compose 内部 MinIO endpoint 与 `.env.docker` 对应凭据；Excel file #906 / Job #1081、DXF #907 / SVG #908，677 bytes；同一组入库/出库/失效操作全部 succeeded，探针对象已清理 |
| Infrastructure / Compose | **110/110 pass** | MySQL、MinIO、Nginx、生产/开发 Compose 和 worker 契约；没有为了探针发布 MinIO 9000/9001 |
| Stage tests | **28 + 28 passed** | 当时的 dwg2dxf、dxf2dwg 证据；已退役 Excel 兼容套件不再列入当前门禁 |
| Frontend build | pass | TypeScript 6、Vite 8；幂等提交、URL 状态、真实后端健康标签和标题区对比度修复进入 production bundle |
| Playwright 全量 | **72 passed，1 skipped** | 73 条浏览器场景；Excel Final 数据控制台、历史/刷新恢复、Local/SQLite 健康文案、CORS 幂等头、转换桥接和视觉回归；外部真实 XLS 样本未配置的成功链路按设计跳过 |
| 浏览器实景复核 | pass | 1440×1000 管理员会话无 console error；后端实际报告 `MySQL 权威数据 · 本地对象存储`，最近刷新、分页、搜索和任务区可见；说明文字计算颜色为 `rgb(185, 206, 216)` |
| Documentation checker | pass | 生成 API 合同、91 个 path / 110 个 operation、数据库 head/表数、配置边界、操作探针与交叉链接一致 |

本轮全量回归首次暴露了一个只在真实 MySQL 并发下出现的竞态：两个请求都在唯一键提交前做普通查询，竞争失败者虽然回滚了 nested transaction，但外层 `REPEATABLE READ` 的 consistent snapshot 仍看不到刚提交的胜者，偶发再次抛出 1062。修复不是吞掉异常或重试 INSERT，而是在唯一冲突后执行 `SELECT ... FOR UPDATE` current read，再核对原请求参数；连续聚焦运行和包含该用例的后端全量均通过。

浏览器还证明自定义 `Idempotency-Key` 会触发 Vite 跨源预检；后端现精确允许该请求头，并由回归测试防止后续删除。健康接口和前端不再把 SQLite/local 环境写成 MySQL/MinIO。Compose MinIO 默认只在内部网络可达，宿主 `.env` 的 `localhost:9000` 和密钥不能代替 `.env.docker`；本次使用临时容器 IP 验证真实 MinIO，没有改写密钥或扩大端口暴露。

最终全页截图位于 `output/playwright/excel-final-production-observability.png`。它显示真实数据库/存储标签、刷新时间、全局指标、上传登记、跨批次检索、批次分页和近期任务；标题说明文字使用项目自有类名控制对比度，不依赖 Ant Design 6 当前渲染出的 HTML 标签。

## 9. 2026-07-14 双向 CAD 转换吞吐证据

本轮使用 24 逻辑 CPU 的本地工作站、ODA File Converter AppImage、独占持久 Xvfb 和用户指定目录 `/home/Creeken/Paper/CAD_research/Data/十份排版/排版1/C区域四节钢柱（宝冶）/2.零件图/1：1零件图`。目录实际包含 135 个 `.dwg`（包括合并图）；所有测量均校验输出数量，并检查 DXF `SECTION/$ACADVER` 头或 DWG `AC10` magic。

旧实现的 16 文件逐文件基线为串行 60.313 秒、16/16 成功；直接把旧 `xvfb-run -a` 调到并发 2/4/8 时分别只有 13/16、11/16、8/16 成功，失败来自 display 选择和清理竞态。改为一个持久 Xvfb 后，同一逐文件 workload 在并发 1/2/4/8 下为 12.334/6.388/3.280/1.937 秒，均 16/16 成功；反向为 12.424/6.344/3.395/1.865 秒，均 16/16 成功。

生产批量路径不为每文件启动一次 ODA，而是按版本目录批处理并自适应分片。可重复命令为：

```bash
cd backend
uv run python ../scripts/cad/benchmark_conversion.py \
  --input-dir '/home/Creeken/Paper/CAD_research/Data/十份排版/排版1/C区域四节钢柱（宝冶）/2.零件图/1：1零件图' \
  --concurrency 1,2,4,8 --direction roundtrip --mode batch \
  --json-output ../output/cad-benchmark-full-tuning.json
```

| 目录分片 | DWG -> DXF | DXF -> DWG | 结果 |
|---:|---:|---:|---|
| 1 | 1.836 s / 73.537 files/s | 1.719 s / 78.551 files/s | 双向 135/135 |
| 2 | 1.358 s / 99.382 files/s | 1.377 s / 98.012 files/s | 双向 135/135 |
| 4 | **1.196 s / 112.840 files/s** | 1.260 s / 107.158 files/s | 双向 135/135 |
| 8 | 1.284 s / 105.117 files/s | **1.209 s / 111.628 files/s** | 双向 135/135 |

综合两向吞吐、CPU 余量和 8 路开始出现的启动竞争，生产默认选择最多 4 个分片、每片至少 8 文件；Celery queue concurrency 8 用于多个批次之间的吞吐。两者含义不同，不能把 8×4 当作单批次固定并发。实时进度使用一个聚合 SSE 连接观察最多 200 个文件，500 ms 推送变化；页面只取消当前方向/文件夹范围的 active Job。

当前源码重启后只保留本地 `dxf`/`dxf2dwg` topology，Compose 的同名 worker 已停止，`scripts/status.sh` 无重复消费者警告。通过 Nginx `:8080` 的真实 32 文件闭环结果如下：DWG -> DXF 批量提交 0.088 秒，SSE 从 32 queued 经 32 running 和分段完成到 32 succeeded 共 6 帧、4.040 秒；派生 DXF 上传 2.730 秒，DXF -> DWG 提交 0.063 秒，SSE 共 7 帧、4.030 秒。32 个 DXF 和 32 个 DWG 均经 result、签名 URL 和 Nginx 实际下载，分别通过 DXF 文本头和 DWG `AC10` magic 校验。另一个 2 文件批次被作用域取消 2/2，再提交后 SSE 1.513 秒收敛为 2/2 succeeded，未调用管理员全局取消。

第一次真实批处理还暴露了 SQLite 测试未覆盖的 MySQL 1020：ODA 已成功，但结果持久化事务先读取 Job/源文件，再由独立事务创建并推进 `file_transfers`，旧 `REPEATABLE READ` 事务锁定该 transfer 时报告“Record has changed since last read”，导致 32/32 结果登记失败。修复按既有 DXF preview 成功模式，在结果 metadata 事务开始前由调用者事务登记 transfer intent 并提交，再推进 transfer、写对象、登记 StoredFile/AnalysisResult 和完成 Job；前后向成功测试要求 `save_bytes_as_file` 显式收到 `transfer_uid`。修复后的上述 32+32 下载闭环证明该错误未复现。

最终自动门禁为 backend **896 passed、6 skipped**（15 条既有 dependency/deprecation warning）、Ruff 无错误、Alembic 无待生成 operation；两个 ODA Stage 各 **30 passed**；TypeScript 6 + Vite 8 production build、Compose config 和文档一致性均通过。双向转换页的实时浏览器聚焦回归为 **8 passed、2 skipped**：单文件上传确实调用批量 Job API，“继续任务”调用批量入口，active 可见性和范围统计均通过；两个“点击全部暂停”用例因各自页面加载时没有 active Job 按设计跳过，作用域取消由前述真实 2 文件 API 闭环和后端授权/状态测试覆盖。

## 10. 2026-07-20 每日归档与数据控制台证据

本轮为数据控制台增加非破坏性的每日一键归档：按 `Asia/Shanghai` 业务日冻结已登记且对象可读的文件，后台任务生成 ZIP 与独立 JSON 清单，再把两个产物写入对象存储并登记到 MySQL。预检令牌绑定日期、桶范围、文件 ID 与清单摘要，执行接口具备权限、幂等、容量上限和源对象二次校验；归档不会移动、删除或软删除源文件，也不替代数据库和对象存储灾备。

| 门禁 | 结果 | 实际覆盖 |
|---|---|---|
| Backend Ruff | pass | 每日归档模型、服务、API、maintenance task、存储流式写入、迁移和测试 |
| Backend 全量 | **1004 passed，6 skipped** | 135.87 秒；15 条既有 dependency/deprecation/test-secret warning，无失败 |
| Alembic | pass | 单一 head `e2f4b8c6a130`；活动 MySQL 增量升级成功，`alembic check` 无待生成 operation |
| 活动数据库 | pass | MySQL 当前共 45 张运行时表，包含 `daily_archive_runs` 与 8 张 Celery 运行时表 |
| Documentation checker | pass | 生成 OpenAPI 为 114 个 path / 135 个 operation；配置、API、数据库、运维、架构和专项设计同步 |
| Frontend production build | pass | TypeScript/Vite production bundle；`dayjs` 固定为直接依赖 |
| Playwright 全量 | **64 passed，1 skipped** | 每日归档预检、二次确认、异步进度、成功结果、ZIP 下载，以及现有上传/工作流/管理页面回归 |
| 真实浏览器只读复核 | pass | 通过 Nginx 登录并打开真实每日归档页；预检 HTTP 200，当前业务日冻结 217 个文件、约 457.1 MiB，页面无 console error |
| 归档产物隔离闭环 | pass | 测试存储实际生成 ZIP、`manifest.json` 和独立 JSON 清单；产物均登记 `files`/`file_transfers`，幂等重放不重复生成 |

真实浏览器复核运行在当前开发栈，后端如实报告 MySQL 和 `local` storage；生产 Compose 的同一存储接口配置为 MinIO。本轮没有点击真实页面的最终执行按钮，因此没有对 217 个现有业务文件生成归档产物，也不把该只读预检描述为真实 MinIO 归档执行。ZIP/清单写入、MySQL 登记、重复提交复用、对象缺失失败和令牌篡改拒绝由隔离服务/API/浏览器回归覆盖。

`bash scripts/db.sh migration-test` 需要非交互 `sudo` 创建临时 MySQL schema，当前会话没有该权限，因此该项未执行成功；没有删改任何数据库。替代证据为活动 MySQL 的旧 head 到 `e2f4b8c6a130` 增量升级、模型/索引对齐修正后的 `alembic check`、SQLite 完整迁移链和全量回归，不能把它表述为临时 MySQL 空库迁移已通过。

## 11. 2026-07-11 基线证据

2026-07-11 文档审计运行使用已有本地 MySQL 和已经运行的本地 Nginx/FastAPI/五个已实现 worker；没有重启 stack 或重建 Compose volume。

| 门禁 | 结果 | 边界 |
|---|---|---|
| Documentation checker | pass | 中文单文档集、生成 API、link、table/head、HTTP/TLS、gitlink 和 production-doc contract |
| Backend Ruff | pass | application、test、verifier、documentation script |
| Backend pytest | **743 passed，3 skipped** | 15 个 dependency/deprecation warning；包含 69 条工作流测试；SQLite 隔离 |
| Alembic check | 无新 operation | 已知 `drawings`/`drawing_versions` cycle warning 仍存在 |
| MySQL migration test | pass | 空临时 schema -> `e4a1c7f2b930`；验证 25 张模型表并清理临时库 |
| Infrastructure verifier | **110/110 pass** | 活动 MySQL 为 34 张表；静态 Compose/Nginx/Dockerfile/env 契约；不含 TLS/build/restore E2E |
| Stage tests | **28 + 28 passed** | 当时的 dwg2dxf、dxf2dwg 证据；已退役 Excel 兼容套件不再列入当前门禁 |
| 工作流框架测试 | **69 passed（包含于后端全量）** | service 状态机、HTTP 认证/访问/校验、生命周期、同步、重新计算 |
| Frontend build | pass | TypeScript 6 与 Vite 8 production bundle；尚无生产流程/基础设施页面 Playwright 用例 |
| Playwright | **68 passed** | 通过现有本地 Nginx/API/worker；含有效 Excel 样本、下载重签名、双向文件页和 Jobs UI；不覆盖新增 workflow API/UI |
| Live read-only verifier | 7 项历史通过 | 运行中旧进程仍为 71 path/88 operation；当前源码生成值为 77/95，新增 route 未经该进程验证 |

全量运行提供仓库已知有效 Tekla 清单，并通过成功 upload -> Celery -> result -> 首次下载失败 -> 新签名 digest 验证。另一个 `阚导出材料表.xls` 探针因缺少必要 `构件编号` 和 `数量` 列被正确拒绝；相关文件名/扩展名不足以证明输入有效。许多其他 Files/Jobs UI 测试使用确定性 route fixture，只证明 UI/API contract，不证明真实对象处理。

## 12. 历史集成记录

仓库此前记录了 2026-07-11 fresh-volume 集成运行，观察为：

- MySQL 从空 volume 迁移到 `a74c2e9f1d30` 并创建 queue-claim index。
- report Job 通过 API -> MySQL broker -> Celery -> MySQL state -> MinIO，下载 SHA-256 匹配。
- MinIO 中断使 readiness 返回 503 且 database 保持 `ok`；恢复后旧对象仍存在。
- 真实 attempt-2 probe 拒绝 legacy 单参数 message，只在 `(job_id, 2)` 投递后完成。

以上四项仅作为 2026-07-11 的带日期历史证据保留。当时没有重启正在运行的本地 FastAPI，实时 `/openapi.json` 仍是旧进程加载的 71 path/88 operation，因此当时新增 route 只由 TestClient/OpenAPI 生成与迁移测试证明。2026-07-13 的当前证据见第 6 节：当前源码为 91 path/110 operation，并已用当前源码 API、真实浏览器和真实 MySQL/MinIO 验证预览与登记链路。通用工作流的自动 Job/产物接线仍是独立范围；DXF→Excel 页面的显式 Excel Final 桥接不改变该边界。

## 13. 故障定位

1. 记录 revision、request ID、Job ID/attempt、时间、flag、sample digest 和准确 entry URL。
2. 不先重启，先检查 `bash scripts/status.sh`、`/health` 和 `/health/ready`。
3. 检查第一处 API/worker/storage/MySQL error，不只看 browser 最终消息。
4. 确认 `alembic current`、Job/JobStep state、queue worker identity 和 Stage source 可用性。
5. 比较 `files.sha256`、storage byte 和 downloaded byte。
6. 区分 browser fixture coverage、真实 backend call 和真实 worker/object result。
7. 增加聚焦 regression，再重跑全部受影响层和必要 E2E 场景。

禁止通过启用内存 fallback、关闭 authorization、接受任意 spreadsheet 内容，或把 skipped scenario 描述为 verified 来让门禁通过。
