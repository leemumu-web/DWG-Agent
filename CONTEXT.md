# DWG-Agent 领域上下文

本文件统一仓库中的业务语言。代码目录、模型、HTTP 接口、Celery 任务、前端页面、测试和文档均应使用这里的名称；不得用含义模糊的 `manager`、`handler` 或 `utils2` 代替领域概念。

## 规范来源

- 目标架构：`/home/Creeken/Paper/CAD_research/结构图/架构设计.txt`
- 业务流程：`/home/Creeken/Paper/CAD_research/结构图/总流程图.mmd`
- 节点拓扑：`/home/Creeken/Paper/CAD_research/结构图/总节点图.mmd`
- 当前事实：仓库代码、Alembic 迁移、OpenAPI、Compose 和自动测试优先于历史说明。
- 已确认的输入差异：当前生产入口由人工上传多个 DWG 和一个 Excel，服务器生成 DXF；不得因目标图中的旧“DXF + DWG + Excel 上传”描述而恢复人工 DXF 上传。

## 核心业务概念

### 生产批次（Production Batch）

一次可追踪的生产输入与处理范围。当前由 `WorkflowRun` 和 `WorkflowInputBatch` 共同表达，未来若引入独立批次模型，必须提供明确迁移路径，不能让两个“批次”概念并存而含义不清。

### 输入冻结（Input Freeze）

在 DWG、服务器派生 DXF 和单个 Excel 完整后，固定输入文件集合与清单哈希。冻结后不可原位替换源文件；修改必须形成新版本或重新进入校验流程。

### 图纸处理单元（Drawing Unit）

以内部 `drawing_id` 关联一份原始 DWG、服务器派生 DXF、分类结果、处理状态和正式结果的稳定业务对象。内部流程不得只靠文件名维持关系。

### 文件登记（File Registry）

MySQL 中关于对象的权限、来源、大小、摘要、桶、键、状态和派生关系。文件字节位于 Local/MinIO 存储，MySQL 不保存大文件内容。

### 文件流转（File Transfer）

跨 MySQL 和对象存储的写入、读取、删除或修复意图及其结算记录。它承担补偿和审计，不等同于业务 Job。

### Job

Linux 平台中的可重试异步业务执行记录。正式状态保存在 MySQL；Celery 消息只触发执行，不是事实源。

### Attempt

同一 Job 的执行代次。当前由 `jobs.attempt` 与 `job_steps.attempt` 表达。旧 attempt 不得修改新 attempt 的状态或正式结果。

### Workflow

跨文件、Job、人工交接和结果的生产流程编排。已实现阶段必须连接真实 Job/Artifact；未实现核心阶段必须暴露明确契约和状态，禁止伪成功。

### DXF 分类运行（DXF Classification Run）

Steel DXF Classifier 1.1.0 对冻结 DXF 集合的一次版本化执行，包含逐图诊断、分类路由和 JSON/CSV/DXF 产物。

### Excel Final

对业务 Excel 进行规范化、拆分、手册查询和最终工作簿生成的独立处理阶段。它拥有关系化批次、零件和构件查询模型，但不拥有文件权限或 Job 状态机。

### Excel Final 输入接入（Excel Final Source Intake）

Excel Final 内部把工作簿或 Tekla 文本可靠转换为统一 `SourcePart`、`ComponentSourceRow` 和输入诊断的 Module。它在同一个 Interface 后选择标准工作簿、初始表、制表符文本或固定宽度文本 Adapter；格式或表头无法唯一判断时拒绝整份输入，已识别输入中的行级问题交给质量流程保留并隔离。

### 五金手册材质路由（Handbook Material Routing）

Excel Final 对 D 系列规格按材质族选择唯一手册类别的规则：HRB 查询螺纹钢，HPB、Q235B、Q355B 查询圆钢。该路由只确定查询类别，不代表手册一定命中；其他材质不得跨类别借用重量。Stage 是规则 implementation，后端 Adapter 只按同一映射校验调用契约，并由跨 seam 测试防止两侧漂移。

### 每日归档（Daily Archive）

按 `Asia/Shanghai` 业务日冻结已登记对象，非破坏性生成 ZIP 与独立 JSON 清单，并将产物重新登记到 MySQL 和配置的对象存储。它不是 MySQL/MinIO 灾难备份。

### 控制平面（Control Plane）

展示 worker、队列、消息、事件和外部 Windows 契约的管理接口。框架存在不代表 RabbitMQ、Outbox、Beat、Windows Node Agent 或 SinoCAM 已实现。

### Stage

可独立测试和版本化的确定性算法包，例如 DWG→DXF、DXF→DWG、DXF→Excel、Steel DXF Classifier 和 Excel Final。Stage 不拥有 HTTP 鉴权、项目权限、Job 生命周期或对象登记。

## 目标但尚未交付的概念

### Outbox

与业务任务同一 MySQL 事务写入的待投递记录。目标用于可靠投递 RabbitMQ；当前仓库尚未实现，整理目录时只能保留真实契约，不得创建伪实现。

### RabbitMQ Broker

目标架构中 Celery 的唯一 Broker。当前运行时仍使用 MySQL SQLAlchemy transport；基础设施目录必须如实区分 current 与 target。

### Celery Beat

目标架构中唯一有效的周期调度实例。当前没有 Compose Beat 服务，maintenance queue 的存在不等于 Beat 已交付。

### Windows Node Agent

目标架构中的低权限 Windows Service，负责节点身份、心跳、租约、传输和恢复协调，不直接操作 SinoCAM。当前只有契约占位。

### CAM Runner

目标架构中运行在专用 Windows 用户交互会话内的执行程序，通过受控 Named Pipe 与 Node Agent 通信。当前未交付。

### SinoCAM Adapter

隔离 SinoCAM 企业 Pipe 协议及版本差异的适配器。当前未取得可验证实现，不能在文档中写成可用能力。

## 架构词汇

- **Module**：具有明确 interface 和 implementation 的代码包或垂直切片。
- **Interface**：调用者必须知道的类型、不变量、错误模式、顺序和配置，而不只是函数签名。
- **Seam**：interface 所在、可替换行为而不修改调用者的位置。
- **Adapter**：在 seam 上满足 interface 的具体实现，例如 Local 与 MinIO adapter。
- **Depth**：一个较小 interface 隐藏较多可靠行为所形成的杠杆。
- **Locality**：同一业务变化、诊断和测试集中在同一 module 的程度。
