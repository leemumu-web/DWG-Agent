# 生产项目与唯一完整工作流设计

日期：2026-07-24

状态：已完成交互确认，待书面审阅

## 背景

当前“新建生产批次”要求用户先选择已有 Project，再创建并启动一个
`linux_production` Workflow。这把 Project、生产批次和完整生产流程表现为三个
不同业务对象，与现场定义不符。

生产业务的真实边界是项目：每次新建生产任务实质上创建一个新项目，每个项目
从生产资料入库开始，经 DWG 入库与 DXF 转换、分类、Excel 处理、后续生产阶段，
直到交付归档，都在同一条完整工作流中完成。

## 目标

- 用户一次提交项目资料，系统原子创建项目及其唯一完整生产工作流。
- 一个生产项目最多存在一个 `linux_production` 工作流。
- 前端以“生产项目”为主对象，不再要求选择已有项目或填写批次名称。
- 创建成功后直接进入唯一工作流详情，继续上传完整生产文件夹。
- 复用现有 Project、Workflow、权限、阶段模板和审计能力。

## 非目标

- 不改变 Files、Excel 和兼容型 `excel_delivery`、`file_delivery` 对 Project 的使用。
- 不删除通用 Project API 或兼容 Workflow API。
- 不改变已确认的文件夹上传、DWG 入库、DXF 主格式和完整 ZIP 下载规则。
- 不在本次实现中补齐仍为 placeholder 或 external 的生产算法。

## 领域边界

Project 是生产业务的主对象，`linux_production` Workflow 是项目唯一的完整执行
状态机。WorkflowRun、WorkflowStageRun 和 WorkflowArtifact 继续承担编排、阶段
与产物引用职责；File、Job 和 AnalysisResult 继续作为字节、执行和结果事实源。

“输入批次”仍可作为工作流详情中的冻结版本技术概念存在，但项目入口、列表、
导航和成功提示不再把 Workflow 称为独立生产批次。

## 创建用例

新增生产项目组合创建接口。请求字段为：

- `code`：项目编号，人工填写，沿用现有字母、数字、下划线和连字符规则；
- `name`：项目名称；
- `description`：可选项目说明。

服务器在一个数据库事务中执行：

1. 复用现有 Project 服务创建项目并把当前用户登记为 `project_owner`；
2. 复用现有 Workflow 服务创建 `linux_production` 工作流；
3. 由服务器生成内部工作流名称，不接受用户提供批次名称；
4. 复用现有启动逻辑生成完整阶段并进入 `source_intake`；
5. 写入项目与工作流审计记录；
6. 提交事务并返回 Project 与 WorkflowDetail。

任一步失败，项目、成员关系、工作流、阶段和审计记录全部回滚，不允许半成功。

## 唯一工作流约束

创建 `linux_production` Workflow 时锁定目标 Project，并检查是否已存在同类型
Workflow。若存在，返回：

- HTTP 409；
- 错误码 `PRODUCTION_WORKFLOW_ALREADY_EXISTS`；
- details 包含 `project_id` 和既有 `workflow_id`。

该检查同时进入通用 Workflow 创建服务，确保旧公开路由无法绕过组合接口。兼容
Workflow 类型不受此约束。

组合创建接口主要处理“新 Project + 唯一 Workflow”，项目编号数据库唯一约束
继续处理并发重复创建。

## 前端信息架构

`/workflows` 页面改为“生产项目”工作台。

### 页头与统计

- 标题：生产项目；
- 说明：一个项目贯穿从资料入库到交付归档的完整工作流；
- 主按钮：新建生产项目；
- 统计：项目总数、进行中、待操作、已完成。

### 项目列表

每一行表示一个生产项目及其唯一工作流：

- 项目编号与项目名称为主信息；
- Workflow ID 和模板名称为次级技术信息；
- 完整流程状态；
- 当前生产阶段；
- 总进度；
- 更新时间；
- “进入项目”操作。

不再分别展示“生产批次”和“项目”两列。点击整行或操作按钮进入
`/workflows/{workflowId}`。

空状态为“还没有生产项目”，主操作为“创建第一个生产项目”。

## 新建生产项目抽屉

采用现有 Ant Design、页面容器、状态组件和生产流程视觉语言，保持克制的工业
工作台风格。

抽屉内容：

- 标题：新建生产项目；
- 说明：创建后立即建立并启动该项目唯一的完整生产流程；
- 步骤：填写项目资料 → 建立完整流程 → 上传生产文件夹；
- 项目编号；
- 项目名称；
- 可选项目说明；
- 准备提示：完整文件夹内至少一个 DWG、恰好一个 Excel，无需准备 DXF；
- 提交按钮：创建项目并进入工作流。

删除以下旧交互：

- 已有项目下拉选择；
- 用户可编辑的批次名称；
- 根据项目和日期生成批次名；
- “无项目请联系管理员”的死路；
- “项目创建成功但工作流启动失败”的半成功提示。

提交期间禁止重复提交和关闭抽屉。失败时保留表单内容并展示结构化错误；成功后
立即跳转工作流详情。

## 错误处理

- `PROJECT_CODE_EXISTS`：定位项目编号字段，保留其他输入；
- `PRODUCTION_WORKFLOW_ALREADY_EXISTS`：说明该项目已有完整流程，并允许进入既有
  Workflow；
- 其他错误：显示结构化错误，不关闭抽屉；
- 不向用户展示已回滚的 Project 或 Workflow。

## 兼容策略

- 既有生产数据按 Workflow 聚合 Project 信息后继续展示；
- 通用 Project 与兼容 Workflow API 保留；
- Dashboard 入口、页面文案、前端契约、OpenAPI、架构文档统一使用“生产项目”；
- 工作流详情 URL 和现有阶段执行逻辑保持不变；
- 不迁移、不复制现有 File、Job、Result 或 Artifact。

## 测试与验收

后端：

- 组合创建成功后存在一个 Project、一个 owner membership、一个已启动的
  `linux_production` Workflow 和完整有序阶段；
- 启动失败时 Project、membership、Workflow 和 stage 均不存在；
- 项目编号重复返回稳定错误；
- 同一 Project 通过组合接口或通用 Workflow API 均无法创建第二条生产 Workflow；
- 兼容 Workflow 仍可按原合同创建。

前端：

- 新建抽屉没有项目选择器和批次名称；
- 项目编号、名称和说明按新合同提交；
- 创建成功后直接进入返回的 Workflow 详情；
- 创建失败保留表单；
- 列表以项目为主信息并只提供一个工作流入口；
- Dashboard 与空状态文案同步。

发布门：

- 后端全量测试；
- 前端生产构建；
- Workflow Playwright E2E；
- 架构快照、分区边界和文档一致性检查；
- 独立代码审查无 Critical 或未处理 Important。
