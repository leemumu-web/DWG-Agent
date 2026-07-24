# 目标架构实现状态与差距

> 本文保留原始逐项调研证据，并作为目标差距的历史基线。2026-07-21 起，仓库结构重构的实时设计与计划见[领域重构设计](../superpowers/specs/2026-07-21-repository-domain-reorganization-design.md)和[实施计划](../superpowers/plans/2026-07-21-repository-domain-reorganization.md)。文中旧路径或旧排除声明仅代表调研时点，不应解释为当前完成状态。

> 2026-07-22 余料库更新：独立 `remnant_inventory` 后端领域、DXF 确定性解析 Stage、六张业务表、21 个 API 操作、`remnant_convert`/`remnant_parse` 专用队列（默认并发 2/4）以及 Web 检索、批量导入、刷新恢复、人工校正、部分确认、预占和原图下载界面已实现。功能仍默认关闭，只有完成[余料库上线与运行手册](../operations/remnant-inventory.md)中的真实样本、材质、权限、备份和回滚门禁后才允许启用。该纵向功能不代表历史审计所述自动拆板或 Windows/SinoCAM 闭环已经实现。

## 历史审计说明

> 2026-07-24 结构更新：后端 platform/bootstrap 已分层，identity、projects、files、jobs、cad_processing、dxf_classification、excel_processing 和 workflows 已完成纵向归域；HTTP、ORM、权限、attempt、Stage 包版本与稳定 Celery 任务名由机器契约锁定。Excel 第一阶段现拥有版本化输入检查和有界错误合同，登记、冻结、worker 执行使用同一规则。`linux_production` 为 revision 2 九阶段框架：多个 DWG + 单 Excel 上传、服务器 DXF、输入冻结、Steel DXF Classifier 和冻结 Excel `excel_stage1` 已接通；DXF→Excel 仅保留独立工具。图纸拆板、CAM 工作包、Windows Node Agent/SinoCAM 和结果接纳仍为明确 placeholder/external。前端列表与独立批次详情分离，当前阶段工作区不会要求二次选择 Excel。本报告其余较早日期章节作为当时审计快照保留。

> 审计日期：2026-07-18
> 审计对象：`/home/Creeken/Paper/CAD_research/complete_framework`
> 目标依据：`/home/Creeken/Paper/CAD_research/结构图/架构设计.txt`
> 审计方法：阅读目标架构、仓库说明、架构/部署/管线/工作流/验证文档，检查 FastAPI、SQLAlchemy 模型、Celery、存储适配、React、Compose、Nginx、各 Stage 与 Windows worker 占位代码，并执行可重复的静态检查、构建和部分测试。

---

## 1. 执行摘要

### 1.1 总体判断

`complete_framework` **不是空骨架**，已经形成了一个较完整的通用 CAD/Excel 文件处理与管理平台，尤其在以下方面已有扎实实现：

- React 19 管理端、FastAPI API、用户认证、RBAC、项目权限；
- 文件上传、登记、SHA-256、Local/MinIO 存储适配；
- 文件流转账本、对象与数据库一致性扫描、补偿处理；
- MySQL 业务模型、Alembic 迁移、审计日志；
- Job/JobStep 状态机、`attempt` 代际隔离、取消、重试和进度；
- Celery 异步执行框架及多个独立队列；
- DWG→DXF、DXF→DWG、DXF→Excel、Excel Final 的独立处理链路；
- SSE 当前状态推送、鉴权下载、DXF SVG 预览；
- Docker Compose、Nginx、MySQL、MinIO、worker 的部署骨架；
- 大量后端、Stage、前端及基础设施测试与验证记录。

但是，若以《架构设计.txt》描述的**钢结构深化设计—人工拆板—SinoCAM 多 Windows 节点排版完整生产闭环**作为唯一目标，当前仓库仍处于“通用平台底座已形成，目标业务主链尚未接通”的阶段。目标中最关键的差异包括：

1. 当前运行时使用 **MySQL SQLAlchemy transport 作为 Celery broker，并使用 MySQL Celery result backend**，目标明确要求 RabbitMQ 为唯一 broker、MySQL 为正式结果事实源且不依赖 Celery Result Backend。
2. 没有目标要求的 transactional outbox（待投递记录表和独立投递程序）。
3. 没有 Celery Beat 周期维护服务和持续租约/心跳恢复机制。
4. 没有“DXF+DWG+Excel 批次接收、真实格式检查、文件名规范化、DXF/DWG 一一配对、输入冻结、图纸处理单元”这一领域模型和状态机。
5. 没有 BH/BOX 分类、自动拆板、左右进提取、独立拆板校验以及人工拆板回流流程。
6. Excel 基础处理和 Excel Final 各自存在，但没有按目标业务屏障与全部图纸结果自动汇合。
7. Windows Node Agent、CAM Runner、SinoCAM Adapter、Named Pipe、节点注册、心跳、租约、fencing token、工作包调度和 CAM 结果验证均未实现。
8. SSE 只保存 Job 的最新快照，没有持久事件表、连续事件号和 `Last-Event-ID` 补发。
9. Compose 只有 HTTP，无 HTTPS/TLS；MinIO 也没有面向 Windows 节点的外部 HTTPS 预签名直传入口。
10. 目标要求的备份、监控、告警、容量和恢复演练仍主要停留在文档层。

### 1.2 进度估算

进度百分比高度依赖权重，不能把 API 数量、测试数量或通用页面数量直接等同于目标业务闭环。按本次审计建议的两种口径：

| 口径 | 估算 | 说明 |
|---|---:|---|
| 通用企业平台底座 | **约 65%–75%** | 认证、权限、文件、存储、Job、审计、前端、Compose 和部分处理 Stage 已较完整 |
| 《架构设计.txt》完整目标 | **约 28%–35%** | 目标核心价值链中的批次配对、拆板、人工回流、Windows/SinoCAM/CAM 闭环基本未实现 |

建议项目管理以第二个数字为主，避免因平台外围功能较丰富而高估最终交付进度。

### 1.3 最关键的项目方向问题

历史审计时仓库说明曾把以下内容冻结或排除；当前以本文件顶部列出的领域重构设计与实施计划为准：

- CAD 构件提取、分类、自动/交互拆板、左右进；
- Windows CAD Worker；
- Agent/model/MCP 执行。

其中前两项恰好是《架构设计.txt》的核心目标。因此，**当前仓库路线图与用户给定目标存在直接冲突**。如果《架构设计.txt》仍是项目最终目标，第一项管理动作不是继续优化现有通用控制台，而是正式修订范围、路线图和验收标准，把拆板及 Windows SinoCAM 子系统重新纳入交付范围。

---

## 2. 当前仓库实际形态

### 2.1 当前实际拓扑

```text
Browser
  -> Nginx（本地 :8080；Compose 宿主默认 :80，仅 HTTP）
     -> React SPA
     -> FastAPI :8010
        -> MySQL（业务数据 + Celery broker/result 表）
        -> Local FS 或 MinIO

Celery workers
  -> MySQL SQLAlchemy transport 领取消息
  -> Local/MinIO 读取与写入文件
  -> ODA / dxf2excel / excel_final Stage
```

### 2.2 主要实现模块

| 层次 | 主要位置 | 当前能力 |
|---|---|---|
| 业务模块 | `backend/app/modules/` | identity、projects、files、jobs、workflows、CAD/分类/Excel、operations 和 automation 按 owner 共置 route/model/schema/use case/task |
| 应用装配 | `backend/app/bootstrap/` | FastAPI router、model registry、task registry 与 seed；不保存业务规则 |
| 平台服务 | `backend/app/platform/` | 配置、数据库、HTTP、Celery/SQL transport、日志、安全和 Local/MinIO 技术 seam；AST 门禁禁止导入业务模块 |
| 异步执行 | `backend/app/modules/*/tasks.py` | 7 个真实 module 注册 11 个历史公共 task name；agent/cad/dispatch 只保留队列契约，无 task |
| 存储 | `backend/app/platform/storage/` | Local 与 MinIO adapter |
| 前端 | `frontend/src/` | 管理、上传、转换、任务、复核、审计、基础设施、Excel Final、工作流页面 |
| CAD 转换 Stage | `Stages/dwg2dxf`、`Stages/dxf2dwg` | 基于 ODA 的双向格式转换 |
| DXF 表格提取 | `Stages/dxf2excel` | DXF 材料表提取；历史损坏 gitlink 已转换为父仓库普通 tracked source，内置测试可从干净 clone 重放，419 文件历史 corpus 仍不分发 |
| Excel Final | `Stages/excel_final` | 表格整理、拆分、手册查询与最终工作簿生成 |
| Windows 执行端 | `windows/` | 已按 Node Agent、CAM Runner、SinoCAM Adapter、协议分层；仍无可执行实现 |
| 基础设施 | `compose.yaml`、`infra/`、`scripts/` | HTTP Nginx、FastAPI、MySQL、MinIO 和多个 worker 的部署/检查脚本 |

---

## 3. 按目标架构逐项对照

状态定义：

- **已实现**：代码路径完整，至少具备自动测试或明确运行证据；
- **部分实现**：已有可复用基础，但与目标契约仍存在关键差距；
- **未实现**：只有占位、配置符号、文档设想，或完全不存在；
- **实现方向不一致**：已有实现，但技术路线与目标明确要求相反。

### 3.1 Web、API、身份和管理端

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| React 19 前端 | 已实现 | 当前依赖 React 19.2.7，具备登录、项目、文件、Job、复核、工作流、数据控制台和 Excel Final 页面 |
| FastAPI 控制平面 | 已实现 | API 路由和 service 分层较完整，具备统一鉴权、错误封装和审计 |
| Nginx 反向代理及 SPA | 已实现 | Nginx 可提供 SPA、API 代理、限流、安全头和 SSE buffering off |
| HTTPS、HTTP→HTTPS | 未实现 | Compose 只发布 HTTP `${HTTP_PORT:-80}:8080`，没有 443 listener、证书和重定向 |
| 生产环境关闭 OpenAPI | 已实现 | 文档和配置约束已覆盖 |
| 用户、角色、项目权限 | 已实现 | 全局 RBAC 与项目成员边界均存在，列表访问大多在 SQL 层先过滤再分页 |
| 人工拆板任务管理 | 未实现 | 没有目标意义上的人工拆板任务、下载输入、上传双格式结果和退回机制 |
| Windows 节点管理 | 未实现 | API router 中没有节点注册、心跳、领取、续租、完成等接口 |

**评价：** Web/API 通用框架是当前完成度最高的部分之一，但尚未承载目标业务的人工拆板与 Windows 节点控制面。

### 3.2 Docker Compose 与基础设施

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| Nginx/FastAPI/Worker/MySQL/MinIO 容器化 | 已实现 | Compose 定义了对应服务、网络、volume、健康检查和重启策略 |
| RabbitMQ | 未实现 | Compose 没有 RabbitMQ 服务；Celery 使用 MySQL transport |
| Celery Beat | 未实现 | Compose 没有 Beat 服务，Celery 配置也没有业务维护 schedule |
| 独立 outbox dispatcher | 未实现 | 没有待投递表和扫描投递容器 |
| 内外网络隔离 | 部分实现 | Nginx 同时连接 public/internal，后端、MySQL、MinIO 在 internal 网络；但 `internal: true` 同时导致 worker 无外部 egress，不利于未来模型/CAD 外部依赖 |
| 持久卷 | 已实现 | MySQL、MinIO 和应用 var 均使用 volume |
| 资源限制 | 未实现/不足 | 当前 Compose 未见目标要求的 CPU、内存等资源限制 |
| 依赖健康检查 | 已实现 | backend 等待 MySQL/MinIO healthy，worker 等待 backend healthy |
| 备份与恢复自动化 | 未实现 | 文档明确承认没有协调备份、自动离机备份和灾难恢复演练 |
| 监控、告警、集中日志 | 未实现 | 没有 metrics、tracing、集中日志、dashboard 或 alert 服务 |

**评价：** Compose 基础结构可用，但离目标生产拓扑仍缺 RabbitMQ、Beat、outbox、TLS、备份与可观测栈。

### 3.3 MySQL、任务状态与可靠性

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| MySQL 为业务事实源 | 已实现 | Job、文件、权限、结果、审计、workflow 等均以数据库为准 |
| Job/JobStep | 已实现 | 有通用 Job 与 attempt-scoped JobStep |
| attempt 幂等隔离 | 已实现 | worker claim、进度、成功/失败写入均围绕 expected attempt，能拒绝旧消息更新新 attempt |
| fencing token | 未实现 | Job 只有整数 `attempt`，没有独立 fencing token/lease token |
| 执行尝试独立模型 | 部分实现 | `attempt` 是 Job 字段，JobStep 带 attempt；没有独立 attempts 表、执行租约、输入哈希、镜像版本和尝试输出目录元数据 |
| transactional outbox | 未实现 | Job 创建后直接向 Celery 发布；没有“同事务写 Job+待投递记录”的表和 dispatcher |
| 周期扫描和自动恢复 | 部分实现 | worker 启动时会把 stale running Job 标记失败；不是 Celery Beat 周期扫描，也不会自动创建新 attempt |
| 任务心跳/执行租约 | 未实现 | running Job 只依赖更新时间和启动恢复，没有持续 heartbeat 与 lease renewal |
| 业务失败和技术失败分类 | 部分实现 | 各管线有结构化错误码，但没有目标拆板/CAM 领域的统一重试分类和人工分支 |
| 批次完成屏障 | 未实现 | workflow 是人工阶段骨架；没有按图纸处理单元计数并原子创建下一任务的批次屏障 |
| CAM 工作包完成屏障 | 未实现 | 没有 CAM 工作包模型 |

**评价：** `attempt` 条件更新是很有价值的可靠性基础，但还不足以替代目标中的 outbox、租约、fencing token、周期恢复和领域屏障。

### 3.4 Celery 和消息系统

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| Celery 执行 Linux 异步任务 | 已实现 | report、dxf、dxf2dwg、dxf2excel、excel_final 队列存在 |
| RabbitMQ 为唯一 broker | 实现方向不一致 | 当前 broker 为 `sqla+mysql+pymysql://...` |
| 不使用 Celery Result Backend 作为正式结果 | 部分符合 | 正式业务结果确实写入 MySQL 模型和 storage，但同时配置了 MySQL Celery result backend |
| `task_ignore_result=True` | 未普遍采用 | 当前 Celery 仍保存 task result，并设置 24 小时过期 |
| durable queue、late ack、prefetch=1 | 部分实现 | `task_acks_late=True`、`task_reject_on_worker_lost=True`、`worker_prefetch_multiplier=1` 已配置；SQL transport 的恢复语义弱于 RabbitMQ |
| 按类型拆队列 | 部分实现 | 已有 report/dxf/dxf2dwg/dxf2excel/excel_final/agent/cad 队列，但没有 validator、CAM prepare/result validation、maintenance 等目标队列 |
| 单 Beat 实例 | 未实现 | 无 Beat service |
| outbox 重复投递下幂等 | 未实现 | attempt 防旧执行存在，但没有 outbox 投递代次和投递状态 |

**评价：** Celery 应用框架已经投入实际使用，但消息基础设施是与目标差距最大的底层决策之一。迁移 RabbitMQ 时应保留现有 Job/attempt/权限/结果模型，而不是推翻业务层。

### 3.5 文件上传、登记、不可变性和对账

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| 文件元数据、大小、SHA-256、对象路径 | 已实现 | `files` 表具有 bucket、storage_key、size、sha256、扩展名、批次名、状态等字段 |
| Local/MinIO 统一 adapter | 已实现 | 两种后端均有实现 |
| DB+对象跨系统补偿 | 已实现 | `file_transfers` 和 storage service 实现 saga/补偿语义，是当前亮点 |
| 上传中临时对象→校验→可用 | 部分实现 | 有 transfer 状态和对象补偿，但 StoredFile 领域状态较简单，未完整体现目标的临时对象/冻结输入版本流程 |
| 原始文件不可覆盖 | 部分实现 | bucket+key 唯一、派生文件新建；但没有明确的原始输入版本冻结领域模型 |
| 对象与数据库周期对账 | 部分实现 | 已有异步存储一致性扫描、finding 和管理员处置；不是 Beat 自动周期任务 |
| MinIO 外部 HTTPS 入口 | 未实现 | MinIO 仅内部网络，Nginx 未提供目标所需外部 HTTPS 对象入口 |
| Windows 预签名直传 | 未实现 | 当前下载使用应用自身的 300 秒 HMAC URL并经 FastAPI鉴权流式读取，不是 Windows 与 MinIO 直接传输 |
| 孤立对象/缺失对象处理 | 已实现 | 数据控制台支持扫描、预检及多类处置 |

**评价：** 文件事务和一致性治理已经超过普通原型水平，并已被生产输入冻结和分类分流复用；仍缺 Windows 节点使用的外部 HTTPS 预签名直传契约。

### 3.6 批次接收、DXF/DWG 配对与输入冻结

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| 一次上传生产批次 | 已实现当前输入切片 | 已按后续确认规则改为“多个 DWG + 恰好一个 Excel”；前端新建后进入独立详情页提供上传、转换、结构化错误和冻结反馈，人工 DXF 被拒绝 |
| `batch_id` 与批次状态机 | 已实现当前输入切片 | `WorkflowInputBatch` 以 `workflow_run_id` 唯一绑定生产流程，保存 `uploading`、转换同步、`ready_to_freeze`、`frozen`、版本和错误信息；它不是全目标通用 `ProcessingBatch` |
| 真实格式、文件头、可读性校验 | 已实现当前输入切片 | `registration.py` 从 Local/MinIO 重读字节并核对 SQL 大小/SHA-256，验证 DWG header/最小长度和 XLS/XLSX 至少一个可见工作表；尚未覆盖所有未来人工拆板/CAM 文件类型 |
| 文件名规范化规则和版本 | 部分实现 | NFKC、首尾/连续空白和 casefold 规则用于检测 DWG 同名冲突，同时保留 `original_name` 与 `normalized_stem`；批次 `version=1` 已保存，但规范化算法尚无独立版本字段 |
| 服务器派生 DXF 与 DWG 一一配对 | 已实现当前输入切片 | 每个 `source_dwg` 条目幂等绑定当前 attempt 的 `convert_dwg_to_dxf` Job 和唯一 `derived_dxf_file_id`；同名、可读性和对象摘要在冻结前复核 |
| 缺失、重复、冲突诊断 | 已实现当前输入切片 | 转换状态、`error_code/error_message`、冻结时 issues 和规范化名称冲突均返回前端；当前通过同一批次内修改/重试修复，没有独立“待修复工单”模型 |
| 冻结输入清单和清单哈希 | 已实现 | `freeze.py` 重新读取全部对象，按稳定顺序生成 canonical JSON，保存 `manifest_sha256`、`frozen_at` 和版本，并通过 Files 删除保护禁止旁路删除 |
| 图纸处理单元 | 部分实现 | 每个冻结 DWG 创建内部 `Drawing`/`DrawingVersion`，输入条目保存 `drawing_id` 并关联源 DWG、派生 DXF；自动/人工拆板状态、零件号和最终结果仍待后续领域模型补齐 |

**评价：** 当前确定范围内的输入接收、服务器转换、诊断、冻结与文件保护已经形成可执行纵向切片；下一步不是重做上传，而是扩展图纸处理单元的拆板/人工回流状态。

### 3.7 DXF 分类、BH/BOX 自动拆板和独立校验

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| DXF 右上角语义分类 | 已实现当前分类切片 | `steel_dxf_classifier_v1.1.0` 读取标题栏截面/规格字段，区分 BH、BOX、PL、RHS 等具体类型；证据不足进入待确认/无法读取，不猜测类型 |
| 保存依据、诊断、算法版本 | 已实现当前分类切片 | `DxfClassificationRun/Item` 保存 Classifier 1.1.0、输入 manifest、来源/输出、候选证据和诊断，JSON/CSV 报告、分流 DXF 同时登记为 File/AnalysisResult；尚未覆盖拆板 validator 的几何检查项 |
| BH/BOX 自动拆板 | 未实现 | 没有标题栏识别、截面解析、视图定位、孔洞、腹板/翼板轮廓、重排等实现 |
| 非 BH/BOX 自动转人工 | 未实现 | 没有人工拆板状态机 |
| 独立拆板结果校验 | 未实现 | 没有轮廓闭合、板件数量、厚度、孔洞、零件映射等独立 validator |
| 候选结果与正式结果分离 | 未实现 | 通用 result/review 可复用，但目标领域未接线 |
| 每 attempt 独立目录/对象路径 | 部分实现 | 分类输出明确写入 `workflows/{workflow_id}/dxf-classification/attempt-{attempt}/...`，Job attempt 能拒绝旧执行；全局独立 fencing token/lease 尚未实现 |
| 输入下载后重新计算 SHA-256 | 已实现于输入/分类切片 | 冻结前从存储重读并核对登记摘要；分类只从已冻结的 `derived_dxf_file_id` 清单取源。未来拆板与 Windows 下载仍需沿用同一强制校验 |

需要特别区分：

- `Stages/dwg2dxf` 和 `Stages/dxf2dwg` 是**文件格式转换**；
- `Stages/dxf2excel` 是**图纸表格文字提取**；
- 它们都不等价于目标要求的**钢结构截面自动拆板**。

**评价：** “预处理、标题栏证据分类、分流、报告、MySQL/对象存储登记”已经平台化；BH/BOX 自动拆板、失败转人工和独立几何校验仍是下一阶段核心留白，不能把分类成功写成拆板完成。

### 3.8 人工拆板回流

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| 自动失败/不支持类型生成人工任务 | 未实现 | 无 ManualSplitTask 模型和路由 |
| 携带原始 DXF、DWG、失败原因和诊断 | 未实现 | 无目标任务 payload |
| 人工下载、处理、上传 DWG+DXF | 未实现 | 通用文件上传/下载可复用，但无人工拆板页面及任务约束 |
| 上传结果配对、校验值、可打开性检查 | 未实现 | 无人工结果专用校验流程 |
| 不合格退回人工待处理 | 未实现 | 无状态机 |
| DXF 用于 CAM、DWG 用于留档 | 未实现 | 无目标结果归属模型 |

**评价：** 这是前端、API、模型和校验都需要新增的完整垂直切片。

### 3.9 Excel 第一阶段处理和图纸屏障

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| Excel 基础处理 | 部分实现/较成熟 | `Stages/excel_final`、backend integration、关系化导入和控制台已实现较多实际处理能力 |
| 生成整理表和 part 表 | 部分实现 | Stage 中存在对应整理、拆分、零件写入逻辑，但需用目标真实样本核对最终字段语义 |
| 与生产任务编排 | 部分接入 | `linux_production` 已定义 `dxf_classification` 与唯一 `excel_stage1` 自动执行契约并复用 Job；Excel 阶段只解析冻结 `source_excel`，DXF→Excel 不在主流程；当前阶段仍按顺序推进，不是批次并行屏障 |
| 等待所有图纸自动/人工结果 | 未实现 | 无图纸处理单元屏障 |
| 合并左右进信息 | 未实现 | 上游左右进识别不存在，Excel Final 无法接收完整目标数据 |
| part 表登记原始/拆板后逻辑路径 | 未实现或不足 | 现有 output 与 result file ID 可映射，但没有目标清单内所有图纸的稳定业务对象引用 |
| 批次级完整性检查 | 未实现 | 无针对零件—图纸—原始文件—拆板结果—MinIO 对象的统一 validator |
| 原子创建最终合并任务 | 未实现 | workflow service 有内部 bind/sync 能力，但公开流程仍为人工骨架 |

**评价：** Excel 算法和平台适配是目标中最接近可复用完成的业务模块，但它目前是一条独立管线，不是整个深化设计流程的第二阶段。

### 3.10 通用 Workflow 编排

| 能力 | 状态 | 说明 |
|---|---|---|
| WorkflowRun/StageRun/Artifact 模型 | 已实现 | 具备阶段、进度、Job attempt 绑定字段和版本化 artifact |
| 创建、启动、人工确认、取消 | 已实现 | API 与 React 页面存在 |
| Job 绑定、同步和重试 | 已实现于自动阶段 | 公开执行端点按阶段能力创建/复用 Job，绑定 `job_id + attempt`，同步 Result、失败/取消并支持新 attempt；不适用于留白阶段 |
| 自动挂接文件/结果产物 | 部分实现 | 输入冻结、分类和 Excel 第一阶段能将受支持的 File/Result 挂接为 artifact；独立 DXF→Excel 仍登记自身结果，但不推进 workflow；拆板、CAM 和结果接纳没有产物实现 |
| 目标钢结构批次工作流 | 部分实现 | `linux_production` 已提供九阶段框架，输入冻结、分类和 Excel 第一阶段可调用现有实现；拆板、CAM 工作包、Windows CAM 和结果接纳明确为 placeholder/external |
| 数据库屏障 | 未实现 | 没有目标的原子 compare-and-set 批次阶段推进 |

**评价：** 这是可复用的编排元数据框架，但不能被视为目标业务 orchestration 已完成。

### 3.11 Windows Node Agent 与 SinoCAM

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| Windows Service Node Agent | 未实现 | `windows/node-agent/` 只有目标契约 |
| 一次性注册令牌 | 未实现 | 无注册接口和模型 |
| 每节点独立凭据、轮换、吊销、隔离 | 未实现 | 只有一个全局 `CAD_WORKER_API_KEY` 配置占位，与目标相反 |
| 节点心跳、boot_id、能力与磁盘状态 | 未实现 | 无 Node 表和 API |
| 本地 SQLite 恢复状态 | 未实现 | 无 Windows 代码 |
| 节点状态机 | 未实现 | 无 offline/idle/reserved/running/suspicious/quarantined 等状态 |
| Agent 主动轮询/长轮询 | 未实现 | 无 claim endpoint |
| CAM 工作包分组与版本 | 未实现 | 无模型、Celery prepare task 或参数策略 |
| 租约、续租、fencing token | 未实现 | 无 lease 模型和接口 |
| 预签名下载/上传 | 未实现 | 无 Windows 任务 manifest 或 MinIO 直传接口 |
| 本机单任务与单实例锁 | 未实现 | 无 Windows 实现 |
| CAM Runner 交互会话程序 | 未实现 | 无可执行代码 |
| Named Pipe 协议与 ACL | 未实现 | 无协议、消息 schema、SID/session 验证 |
| SinoCAM Adapter | 未实现 | 无企业 Pipe 适配层 |
| Job Object 进程树控制 | 未实现 | 无 Windows 进程管理代码 |
| 分阶段超时与无进度检测 | 未实现 | 无 Runner |
| 结构化 CAM 结果清单 | 未实现 | 无 manifest schema |
| Linux CAM 结果校验 Worker | 未实现 | 无 CAM result validator task |
| CAM 批次屏障和最终结果包 | 未实现 | 无相关领域模型/任务 |

**评价：** Windows/SinoCAM 子系统为 **0→1 阶段**。仓库已经删除会误导接手者的
`integrations/zwcad` 空占位文件，只在
`backend/app/modules/automation/contracts/` 暴露机器可读的未实现状态；不能因为存在
`cad` 队列名、配置项或合同接口而计入实现进度。

### 3.12 SSE 和事件恢复

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| SSE 展示 Job 状态 | 已实现 | 前端 EventSource 与后端短事务轮询存在 |
| Nginx 关闭 buffering/cache | 已实现 | 配置与静态验证覆盖 |
| 重要事件写 MySQL 持久事件表 | 未实现 | 只在 `jobs.progress_data` 保存最新快照 |
| 连续事件编号 | 未实现 | 无 event sequence |
| `Last-Event-ID` 断线补发 | 未实现 | 重连只发送当前权威快照，历史中间事件不可恢复 |
| 批次、人工任务、节点、CAM 全域事件 | 未实现 | 当前主要围绕 Job/Job set |

**评价：** 当前实现适合“观察最新任务状态”，不满足目标的可追溯事件流和断线补发语义。

### 3.13 安全、运维与生产准备度

| 目标要求 | 状态 | 审计结论 |
|---|---|---|
| API 认证/RBAC | 已实现 | 边界较完整，已有对抗性权限测试 |
| MinIO/MySQL 不直接公开 | 已实现 | Compose internal 网络符合目标原则 |
| Windows 每节点独立身份 | 未实现 | 无 Windows 节点系统 |
| HTTPS | 未实现 | 仅 HTTP |
| 低权限容器用户 | 部分实现 | backend/frontend 有 non-root 和 no-new-privileges；数据库/MinIO按镜像运行 |
| 备份、保留、恢复验证 | 未实现 | 路线图列为待办 |
| 容量告警 | 未实现 | 无监控系统 |
| 恶意文件扫描/隔离 | 未实现 | 路线图列为待办 |
| 处理进程资源隔离 | 部分实现 | Excel Final 有独立子进程和超时，ODA 有超时；未形成统一 CPU/memory/disk/output sandbox |
| 结构化日志与关联 ID | 部分实现 | request ID、Job ID/attempt 和日志基础存在；无集中采集、指标和告警 |

---

## 4. 已经实现得较好的部分

### 4.1 Job attempt 防旧执行覆盖

`Job.attempt`、`JobStep.attempt` 及 service 中的条件领取/更新，已经实现了重要的“执行代际”概念。旧消息或旧 worker 在 attempt 不匹配时无法继续更新当前 Job，这与目标 fencing 思想方向一致。

建议保留并扩展：

- 新增独立 `job_attempts` 表；
- 给每次尝试生成不可预测 fencing token；
- 保存 input manifest hash、算法版本、镜像版本、租约和输出前缀；
- 所有对象接纳和正式结果更新同时匹配 Job、attempt、fencing token。

### 4.2 文件流转 saga 和存储一致性治理

当前 `file_transfers`、Local/MinIO adapter、补偿清理、storage scan/finding/remediation 是仓库中成熟度很高的部分。它正确承认 MySQL 与对象存储无法共享单一 ACID 事务，而不是假装一次 commit 可以同时覆盖数据库和对象。

这套基础可以直接扩展到：

- 原始上传临时路径与可用状态；
- 每个拆板 attempt 的独立候选对象目录；
- Windows CAM attempt 的独立上传目录；
- 旧 fencing token 上传结果的诊断保留；
- 批次清单对账和正式结果接纳。

### 4.3 权限和审计基础

用户、角色、项目成员、文件/Job/result 继承权限和审计日志已经形成较完整边界。未来新增人工拆板、节点管理和 CAM 结果时，应复用现有 helper，不要在新 route 中重复手写权限逻辑。

### 4.4 CAD 双向转换工程化

DWG↔DXF 的 ODA adapter、批量分片、进度、结果登记和实际吞吐验证较成熟。它不是自动拆板，但可作为目标系统的辅助能力，例如：

- 对人工回流文件做可打开性/格式验证；
- 必要时生成兼容格式；
- 提供输入诊断；
- 作为独立工具链保留，而不是误并入拆板算法成功率。

### 4.5 Excel Final Stage

Excel 第一阶段已有独立 Stage、进程隔离、版本化输入探测、唯一源手册数据库读取、关系化导入、批次/零件/构件查询和四标签前端工作台。生产 workflow 直接使用冻结的唯一源 Excel，不再拆成 DXF→Excel 与 Excel Final 两个阶段。

后续重点不是重写 Excel Final，而是定义其正式输入/输出契约，并把它挂到“全部图纸处理完成”数据库屏障之后。

### 4.6 测试和文档治理

仓库有广泛 pytest、Stage test、Playwright、Compose/Nginx 静态验证、文档生成和一致性检查。文档也多次主动说明“代码存在不等于能力已交付”，整体求实程度较好。

需要改进的是：当前仓库文档的“明确非目标”与用户目标冲突，必须先纠正范围，之后测试体系才能围绕真正目标继续扩展。

---

## 5. 当前实现中的关键缺口与风险

### 5.1 P0：项目范围与目标冲突

**现象：** 当前路线图明确冻结拆板和 Windows worker，而目标架构把它们作为主体。

**风险：** 团队可能继续完善转换控制台、Agent 占位或通用管理功能，却不推进最终生产价值链。

**当前处置：** 本轮重构先建立机器可校验的模块目录和目标追踪矩阵，并同步 README、模块文档与验证证据；目标章节不得仅靠说明文字标记为完成。

### 5.2 P0：broker/outbox 与目标可靠性模型不一致

**现象：** 当前 Job commit 后直接发 Celery 消息，broker 为 MySQL SQL transport。

**风险：**

- Job 已提交但消息发布失败时没有 durable outbox 自动补投；
- SQL transport 在整个 worker/主机死亡后的恢复能力有限；
- 业务表与 broker/result 表竞争同一 MySQL 资源；
- 与目标 RabbitMQ 队列、持久化、late ack 和路由拓扑不一致。

**建议：**

1. 新增 `task_outbox` 表；
2. Job/后续任务与 outbox 同事务创建；
3. 新增 dispatcher service；
4. Compose 增加 RabbitMQ；
5. Celery 消息只带 `task_id/job_id + attempt_id`；
6. 正式结果仍只落业务表和 MinIO；
7. 关闭不必要的 Celery result，或明确仅作短期诊断且不参与正确性。

### 5.3 P0：缺少目标批次领域模型

不能仅在 `files.batch_name` 上继续堆逻辑。建议最少增加：

- `processing_batches`；
- `batch_input_versions`；
- `batch_files`；
- `file_validation_results`；
- `drawing_units`；
- `drawing_file_pairs`；
- `manual_split_tasks`；
- `split_candidates` / `split_results`；
- `validation_runs` / `validation_items`；
- `batch_barriers` 或可原子比较的阶段/计数列。

### 5.4 P0：核心拆板算法和独立校验缺失

目标系统不是一般格式转换平台。需要单独建立 tracked Stage，例如：

```text
Stages/steel_split/
  classifier/
  titleblock/
  section_parser/
  view_locator/
  holes/
  contour_rebuild/
  bh/
  box/
  layout/

Stages/steel_split_validator/
  contour_checks/
  topology_checks/
  hole_checks/
  part_mapping/
  report/
```

拆板与 validator 应分离依赖和算法逻辑，避免同一个错误同时判定“生成成功”和“验证成功”。

### 5.5 P0：Windows/SinoCAM 全子系统缺失

当前已经按三个进程产品和协议边界拆分目录，后续实现不得重新合并成一个模糊的 worker：

1. `windows/node-agent`：Windows Service、HTTPS、节点身份、SQLite、租约、传输、恢复；
2. `windows/cam-runner`：交互会话程序、Named Pipe、单实例、Job Object、超时与输出监控；
3. `windows/sinocam-adapter`：企业 Pipe 接口适配、版本兼容和错误映射。

Linux 侧同时需要 node/lease/work-package/result models、API 和 Celery prepare/validate/finalize task。

### 5.6 已解决的历史 P0：`Stages/dxf2excel` 无法从干净 clone 恢复

2026-07-18 审计时，父仓库把它记录成 gitlink `86e99dce5ebce992273c7df78ca13d58036f7472`，但没有 `.gitmodules`，当时全新 clone、CI 和 Docker build 不能保证得到源码。

该阻断项现已解决：`Stages/dxf2excel` 是普通 tracked directory，backend editable dependency 和 Docker build context 不再依赖不可还原 gitlink。仍未随仓库分发的是 419 文件历史 corpus 与生成物；这属于真实样本验收缺口，不是源码引用缺失。

### 5.7 P1：SSE 不具备历史回放

当前 `Job.progress_data` 只保留最后一个事件，无法审计中间过程。建议新增 append-only `domain_events`：

- 自增或按 stream 连续 `event_id`；
- stream type/id；
- batch/job/drawing/node/CAM 关联；
- attempt；
- event type、payload、created_at；
- SSE 读取 `Last-Event-ID` 后补发；
- 定义保留和归档策略。

### 5.8 P1：生产基础设施未完成

目标投入生产前至少还需要：

- TLS termination、证书续期和 Secure cookie 实测；
- MySQL+MinIO 协调备份和恢复演练；
- queue depth/age、Job duration/failure、worker、DB pool、MinIO、磁盘指标；
- 告警和 runbook；
- 统一日志关联 request/batch/job/attempt/node/lease；
- 临时目录和失败诊断保留策略；
- 恶意文件扫描和处理 sandbox；
- 容量与并发压测。

---

## 6. 推荐实施路线

### 阶段 0：统一目标和修复可复现性（立即执行）

完成标准：

1. 确认《架构设计.txt》为规范性目标；
2. 删除文档中“拆板和 Windows worker 不交付”的冲突声明；
3. 建立目标能力矩阵和验收用例；
4. 修复 `Stages/dxf2excel` gitlink（已完成，现为普通 tracked source）；
5. 保护现有稳定的文件、权限、Job 和 Excel Final 能力；
6. 建立干净 clone、依赖安装和镜像构建门禁。

### 阶段 1：可靠消息与执行代次基础

完成标准：

1. Compose 加入 RabbitMQ；
2. Celery 切换 RabbitMQ broker；
3. 增加 outbox 表和 dispatcher；
4. 增加 `job_attempts`、lease、fencing token、input manifest hash；
5. 输出对象统一按 attempt 独立前缀；
6. 增加单 Celery Beat 与维护任务；
7. 覆盖发布丢失、重复消息、worker kill、容器重启和旧结果提交测试。

### 阶段 2：批次输入、配对和冻结

完成标准：

1. 建立 processing batch 与 input version；
2. 支持 DXF/DWG/Excel 整批上传；
3. 内容类型、header、可读性、size、SHA-256 校验；
4. 带版本的文件名规范化；
5. DXF/DWG 唯一配对和冲突报告；
6. 前端补件/替换；
7. 输入冻结和清单哈希；
8. 每对文件创建 drawing unit。

### 阶段 3：拆板最小可用闭环

建议先只做一个类别，例如 BH：

1. BH 分类与诊断；
2. BH 自动拆板候选结果；
3. 独立 validator；
4. 通过后正式接纳；
5. 失败转人工任务；
6. 人工下载 DWG/DXF、上传 DWG/DXF；
7. 人工结果校验与退回；
8. 单批次多图纸数据库屏障。

BH 稳定后再增加 BOX，其他类型继续走人工分支。

### 阶段 4：Excel 两阶段闭环

完成标准：

1. Excel 基础处理与图纸处理并行；
2. 左右进信息形成结构化领域数据；
3. 最后一张图纸 ready 时原子创建 Excel Final Job；
4. 最终工作簿写稳定 file/business IDs，不写主机绝对路径或过期 URL；
5. 批次级零件—图纸—对象完整性校验；
6. 前端展示自动/人工来源和缺失诊断。

### 阶段 5：Windows 节点控制面与模拟 Runner

在接 SinoCAM 前先用模拟器验证分布式协议：

1. 节点注册、独立身份、吊销；
2. heartbeat、boot_id、能力和状态机；
3. 工作包模型、原子 claim、lease、renew、fencing；
4. Node Agent 本地 SQLite；
5. 预签名 MinIO 直传；
6. 模拟 CAM Runner 和 Named Pipe；
7. 断网、重启、旧 token、重复完成通知、磁盘不足场景。

### 阶段 6：SinoCAM Adapter 和真实节点试点

完成标准：

1. 获得并冻结企业 Pipe 文档、版本和错误码；
2. 明确常驻进程还是每任务进程；
3. 实现 Runner/Adapter、Job Object、超时、取消和清理；
4. 生成结构化结果清单；
5. Linux CAM result validator；
6. 先部署 1 台节点，通过真实样本和故障注入；
7. 再扩到 10–20 台普通办公节点。

### 阶段 7：CAM 批次屏障、最终包和生产运维

完成标准：

1. CAM work package 分组策略；
2. 全工作包完成屏障；
3. 最终结果包和确定性 manifest；
4. 持久事件表和 SSE replay；
5. TLS、备份、监控、告警、容量测试和恢复演练；
6. 形成可签署的生产验收记录。

---

## 7. 建议的验收测试清单

### 7.1 批次与文件

- 同名 DXF/DWG 正常配对；
- 缺 DWG、缺 DXF、重复文件、大小写差异、Unicode 名称、规范化冲突；
- 上传中断、MinIO 写成功但 DB 失败、DB 记录存在但对象缺失；
- 输入冻结后替换文件必须生成新 input version；
- 原始文件永不被派生结果覆盖。

### 7.2 Celery/outbox

- Job+outbox 同事务；
- RabbitMQ 不可用时 outbox 保留；
- dispatcher 超时造成重复发布；
- 同一消息重复消费；
- worker 在上传前/上传后/DB commit 前死亡；
- stale attempt 不能完成新 attempt；
- Beat 只运行一个有效实例。

### 7.3 拆板和人工回流

- BH/BOX 正确分类及诊断；
- 不支持类型直接人工；
- 自动候选通过和不通过 validator；
- 自动业务失败不无限重试；
- 技术失败有限重试后转人工；
- 人工只上传一个格式时拒绝；
- 人工文件不可打开、零件号不一致时退回；
- 所有 drawing unit ready 时只创建一个 Excel Final Job。

### 7.4 Windows/CAM

- 两节点并发 claim 只能一个成功；
- 节点断网进入 suspicious 而非立即重复调度；
- lease 到期后新 attempt 获得新 fencing token；
- 旧节点上传可以保存诊断对象，但不能成为正式结果；
- Agent 重启、Runner 仍运行的协调；
- Windows 重启 boot_id 变化；
- 磁盘不足、许可证不可用、Runner 未连接、用户会话不存在；
- 结果清单缺文件、hash 错、零件遗漏/重复；
- 重复完成通知幂等；
- 主服务器接纳后才清理节点工作目录。

### 7.5 SSE 与恢复

- 事件 ID 连续；
- 浏览器携带 `Last-Event-ID` 后补发；
- FastAPI 重启后仍可从 MySQL 恢复；
- 批次、人工任务、节点、CAM 和最终完成事件均可追踪；
- SSE 不作为业务状态事实源。

---

## 8. 2026-07-22 重构发布验证结果

### 8.1 通过项

| 验证 | 本次结果 |
|---|---|
| 文档/架构 | 134 个分区 README、12 个模块、36 张 ORM 表、135 个 HTTP operation、11 个稳定 Celery task 与 10 条任务路由均通过机器门禁 |
| Backend 全量 | 1093 passed、6 skipped、21 warnings；新增预留路由与分区说明完整性回归，没有删除测试，跳过项保留真实外部/MySQL 条件 |
| Alembic | 单一 head `e2f4b8c6a130`，`alembic check` 无新增 upgrade operation |
| Frontend | 106 个 TypeScript 源文件、11 个 feature、production build 通过；最终统一门禁 Playwright 93 passed、5 skipped，同源码三轮均无失败 |
| `Stages/dwg2dxf` | 30 passed |
| `Stages/dxf2dwg` | 30 passed |
| `Stages/dxf2excel` | 17 passed |
| `Stages/steel_dxf_classifier_v1.1.0` | 52 passed |
| `Stages/excel_final/tests` | 最终规范流程全套通过 |
| `docker compose config --quiet` | 通过 |
| `infra/verification/verify.sh` | 122/122；Nginx/Compose/Dockerfile/环境/路径、活动 MySQL 45 表、种子、权限与时间列全部通过 |
| 隔离 MySQL 迁移 | 临时 schema 从空库执行 17 个 revision 到 `e2f4b8c6a130`，验证 36 张业务表与管理员种子后自动清理 |
| 当前进程 | 八组 worker 使用官方 platform Celery 入口，FastAPI `:8010` 与源码一致，liveness/readiness 均为 `ok` |

### 8.2 仍未形成通过证据的项目

本地 Nginx 已改为仓库用户管理并使用自有上传临时目录，`:8080` 的 SPA、健康/就绪和完整
Playwright 均在最终源码上重新通过；旧 root 日志只做保留性改名，没有删除。空 MySQL 迁移
已经在最终统一门禁中通过，不再列为阻塞项。

仓库未部署 RabbitMQ，且没有真实拆板、Windows Node Agent 或 SinoCAM 实现，因此不能执行这些
目标 E2E。本轮也没有向生产项目写入业务 DWG；ODA 与 Classifier 的自动化/Stage 证据不能代替
获准样本的人工质量验收。当前 MinIO/Local 存储与 SQL 登记行为由 files/分类集成测试和既有临时
对象闭环覆盖，未把未执行的生产对象写入验证描述。

### 8.3 发布工作树要求

浏览器截图、运行日志、pid、`__pycache__` 和临时输出不属于源码发布物。正式推送前必须再次执行
`git diff --check`、查看完整 staged diff、确认没有退役包或临时产物，并在推送后证明本地 `HEAD`
与 `origin/main` 相同。该要求是发布门禁，不用“曾经测试通过”替代当前 Git 状态。

---

## 9. 最终结论

当前 `complete_framework` 已经完成了一个质量较高的“企业文件处理平台底座”，其中认证权限、文件与对象一致性、Job attempt、审计、转换工具、Excel Final、管理端和部署测试都具有明显复用价值。

它已经形成《架构设计.txt》目标中的服务器前段切片：操作员提交多个 DWG 与一个 Excel，服务器
完成 DWG→DXF、摘要复核和输入冻结，再由 Classifier 1.1.0 预处理、分类、分流，并把源文件与
派生文件登记进 MySQL 和对象存储。它尚未完成完整生产系统，剩余的不是少量页面，而是四个完整领域：

1. **BH/BOX 自动拆板、独立几何校验和人工拆板回流；**
2. **图纸完成屏障、左右进合并、批次级 Excel 最终一致性与 CAM 输入清单；**
3. **基于 RabbitMQ/outbox/Beat/lease/fencing 的目标可靠性体系；**
4. **Windows Node Agent、CAM Runner、SinoCAM Adapter 和 CAM 工作包闭环。**

因此，最准确的进度表述是：

> **平台底座、生产输入冻结和 DXF 分类分流已形成可执行服务器切片；自动拆板、人工回流、最终屏障与 SinoCAM 分布式生产后半链仍未形成。**

下一步应按“BH 最小拆板闭环 → 人工回流/独立校验 → Excel 屏障 → RabbitMQ/outbox/lease 加固 → Windows 模拟节点 → 真实 SinoCAM”的顺序推进；复用现有 Files、Job attempt、Workflow artifact 和分类账本，避免另建一套文件或任务事实源。
