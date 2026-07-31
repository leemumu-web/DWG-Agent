# BH 左右进 Excel 第二阶段（方案 A）设计规格

## 1. 目标

在 `linux_production` 工作流的“Excel 第二阶段处理”中增加“处理 BH 的左右进”能力：

1. 只读取本工作流第二阶段分流分类已经登记为 `BH` 的拆板前 DXF。
2. 使用 BH 左右进读取器 1.2.7 形成完整的左右进 Excel 审计表。
3. 以冻结原始 Tekla Excel 和 Excel 第一阶段正式结果为依据，重新建立第一阶段规范模型。
4. 将读取到的腹板、相同翼板、不同上翼/下翼和多块翼板映射到“整理表”。
5. 同步重建 `part`，重新计算数量、下料长度、理论重量和所有公式缓存。
6. 任务完成后提供单独的第二阶段 Excel 下载，并保留左右进读取表供核对。

本设计不读取拆板阶段产物，不依赖拆板是否成功，也不修改 gg 上的现有部署或数据。

## 2. 已确认边界

### 2.1 正式输入

第二阶段只接受服务器自己解析的不可变输入，不接受客户端传入文件 ID、分类运行 ID 或项目 ID：

- 当前工作流冻结输入批次中的唯一 `source_excel`；
- 当前工作流 `excel_stage1` 当前 Job attempt 的唯一 `stage1_excel`；
- 当前工作流 `dxf_classification` 当前 Job attempt 的分类账本；
- 该账本中 `disposition=classified`、`part_type=BH`、`next_stage_eligible=true` 的拆板前 DXF。

明确排除：

- `drawing_processing` 产出的拆板后 DXF；
- 其他工作流、其他项目或旧 attempt 的分类结果；
- 用户本地下载后修改过但没有重新登记的 Excel；
- 分类目录扫描、MinIO 前缀扫描或文件名猜测出来的未登记对象。

因此模板合同改为：

- `required_inputs = [stage1_excel, classified_dxf]`
- `artifact_types = [bh_setback_excel, stage2_excel]`
- `required_outputs = [bh_setback_excel, stage2_excel]`

### 2.2 方案 A 的重建含义

第二阶段不在现有 xlsx 中插入、复制或平移零散单元格。实现应复用第一阶段的读取、分类、五金手册、质量检查、拆板投影、part 构建和六 sheet 写表能力：

1. 从同一冻结原始 Excel 重新建立第一阶段规范化内存模型；
2. 从已存第一阶段 Excel 读取规范签名并与重建基线核对；
3. 只有基线一致时才向内存模型注入 BH 左右进；
4. 以第一阶段 Excel 的 `原表` 为源，删除并重建 `清洗表`、`构件表`、`整理表`、`part`、`处理报告`；
5. 公共写表器继续负责列顺序、样式、列宽、公式和公式缓存。

这样既允许“删除重新生成”，又防止升级后的代码静默改变第一阶段无关数据。

### 2.3 不做的事情

- 不把 DXF→Excel 放回主流程。
- 不重新扫描分类文件夹。
- 不使用拆板后的图纸推断左右进。
- 不把 Reader 的低置信度或失败数值冒充正常左右进；允许按本规格保留空值占位行，并明确要求人工补录。
- 不把 5000 个文件 ID 塞入 Job JSON。
- 不把读取器完整诊断 JSON 当作正式产物；它在 199 张实测中已达 4.6 MB，放大到 5000 张会造成无意义的数据库和内存压力。

## 3. 不同项目和不同 attempt 的隔离

### 3.1 服务端绑定

执行请求仍只允许：

```json
{"execution_kind": "excel_stage2"}
```

请求模型保持 `extra=forbid`。即使客户端构造 `project_id`、`file_id`、`classification_run_id` 或 `stage1_job_id`，也必须返回参数校验错误，不能采用这些值。

在同一数据库事务和 `WorkflowRun FOR UPDATE` 锁内完成以下核验：

- `workflow.project_id == stage1_job.project_id`
- `workflow.project_id == classification_run.project_id`
- `workflow.project_id == classification_job.project_id`
- Stage 记录的 `job_id/job_attempt` 与对应 Job、Run 完全一致
- Stage 1 Artifact 的 metadata、AnalysisResult、StoredFile 指向同一 Job attempt
- 分类 Run 的 `workflow_run_id`、Job、attempt、manifest 和状态全部一致
- 每个 BH item 都属于该 Run，每个输出文件可用且为 `.dxf`

任何不一致都以 409 失败关闭，不能退回“最新文件”或其他项目的同名文件。

### 3.2 不可变清单

Job 参数只保存小型、可核验的服务器生成摘要：

```json
{
  "workflow_id": 6,
  "project_id": 6,
  "source_excel_file_id": 123,
  "source_excel_sha256": "...",
  "stage1_job_id": 456,
  "stage1_job_attempt": 1,
  "stage1_excel_file_id": 789,
  "stage1_excel_sha256": "...",
  "classification_run_id": 10,
  "classification_job_id": 11,
  "classification_job_attempt": 1,
  "bh_input_count": 111,
  "bh_manifest_version": 1,
  "bh_manifest_sha256": "..."
}
```

`bh_manifest_sha256` 由按 classification item ID 稳定排序的以下字段生成：

```text
item_id\0output_file_id\0stored_sha256\0output_name\0profile_normalized\n
```

Worker 开始时重新查询同一 Run 并重算摘要。数量或任一字段改变时立即失败，不继续下载。

### 3.3 工作目录

- 使用 `/app/var/excel-stage2-work/{workflow_id}/{job_id}/attempt-{attempt}`。
- 目录权限为当前容器用户私有；不使用项目名称或用户输入拼路径。
- 每个下载文件使用 `classification_item_id.dxf` 作为物理文件名，原名只作为逻辑元数据。
- 单文件完成后及时删除；任务结束、取消或失败都清理整个 attempt 目录。
- 不使用 768 MiB 的 `/tmp` tmpfs 保存整批图纸。

两个项目即使包含相同文件名、相同零件号或同时执行，也不会共享目录、清单、Job、Artifact 或数据库批次。

## 4. BH 读取器接入

### 4.1 版本与代码归属

将 `/home/Creeken/Paper/CAD_research/LaR/BH左右进读取器_v1.2.0_正式发布` 的正式 1.2.7 核心以受控 Stage 包纳入主仓库和 backend 锁文件，生产运行不得依赖仓库外绝对路径。

保留经过验证的：

- ASCII 和 ezdxf 读取；
- 单位校验；
- 腹板、翼板、上翼、下翼和多块翼板识别；
- 安全值取整规则；
- 结果表与三步诊断表格式；
- 对不支持几何的失败关闭行为。

后端固定使用无图片模式。图片、汇总图和 matplotlib 渲染不进入生产第二阶段。

### 4.2 批处理服务

读取器新增可复用的批服务，而不是从后端调用现有 CLI 的 `main()`：

```python
def analyze_manifest(
    entries: Iterable[BhInputEntry],
    *,
    backend: Literal["ascii", "ezdxf", "auto"],
    on_progress: Callable[[BhProgress], None],
) -> BhBatchOutcome:
    ...
```

CLI 与 backend 都调用这一服务，几何分析器不复制两份。

现有 CLI 在 `--no-visuals` 时仍把所有 `DrawingData` 留在列表中；批服务应在每张图分析后立即释放几何对象，只保留生成结果表和 Excel 深化所需的紧凑字段。正式任务不生成完整 JSON。

### 4.3 5000 张规模

本机实测 ASCII、无图片处理 199 张真实 BH：

- 199/199 `OK`
- 420 条板件记录
- 23.603 秒
- 约 0.119 秒/张

按纯计算线性估算，5000 张全为 BH 时约 10 分钟，尚未包含 MinIO 下载和 Excel 重建。因此设计为：

- 独立 `excel_stage2` Celery 队列，不阻塞 Excel 第一阶段；
- 生产默认 worker concurrency 为 1，完成并发压力门后才允许配置为 2；
- 最多预取 2 张，主线程分析当前张，磁盘同时存在的输入文件有固定上限；
- 文件元数据一次批量查询，禁止 5000 次 `db.get()` 的 N+1；
- Job 进度每 1 秒或每约 0.5% 更新一次，最多约 200 次数据库写入；
- 超时单独配置，默认 7200 秒；
- 每个进度事件只写计数、阶段和当前原名，不写结果数组。

进度分段：

| 百分比 | 阶段 | 真实依据 |
|---:|---|---|
| 0–8 | 校验来源 | 已完成的 DB/对象/摘要检查 |
| 8–15 | 建立清单 | 已核验的 BH 总数和清单摘要 |
| 15–75 | 逐图读取 | `processed_files / total_files` |
| 75–88 | 重建整理表与 part | 已完成的模型转换和公式生成 |
| 88–96 | 复核与入库 | 工作簿合同、公式缓存和 MySQL 导入 |
| 96–100 | 保存正式结果 | 两个对象均成功登记后才到 100 |

## 5. 读取结果与 Excel 的匹配

### 5.1 Excel 中的 BH 出现

每个 BH 源零件出现使用以下复合身份：

```text
(来源sheet, 来源行, 序号, 构件编号, 零件号)
```

第一阶段规范模型必须为每个有效 BH 源行形成恰好一条 `BH腹` 和一条 `BH翼` 基线记录。不能只靠相邻行配对。

### 5.2 图纸匹配

- Reader 的 `part_number` 规范化后与 Excel `零件号` 匹配。
- 同一零件号在多个构件中出现时，一份图纸结果扇出到所有出现；每个出现仍使用自己的构件数、原数量和长度。
- 同一项目中 Reader 零件号重复时不比较内容、不自动去重，整批失败并列出全部冲突文件；不同项目之间不参与判重。
- Excel 中同一来源身份不唯一属于基线损坏，整批失败。
- Excel BH 找不到图纸时允许部分发布：该 BH 保持第一阶段角色、长度和理论重量口径，左右进保持未深化状态，并以红色警告明确列出。
- 图纸存在但 Reader 无法可靠读取时允许部分发布：不得沿用原长度冒充成功，整理表和 `part` 保留红色占位，左右进、下料长度及依赖下料长度的理论重量留空。
- 图纸有而 Excel 没有时不写入整理表，只在报告和前端列出“图纸未进入 Excel”的警告。
- 第一阶段 Excel 与分类账本均无 BH 时执行空操作成功，仍生成空的左右进审计表和业务数据等价的第二阶段 Excel。

### 5.3 规格三方核对

执行前后核对：

1. 分类项 `profile_normalized`；
2. Reader `specification`；
3. Excel 拆板行重建的 BH 截面：`H=腹板宽+2×翼板厚，B=翼板宽，tw=腹板厚，tf=翼板厚`。

三方不一致时禁止套用左右进，不能因零件号相同而忽略规格差异。对应 BH 按“读取无效”形成空值占位和红色人工处理项；若冲突破坏第一阶段基线或项目绑定，则仍整批失败。

## 6. 整理表深化规则

### 6.1 角色映射

| Reader 角色 | 整理表类型 | 导入零件号 | 数量倍率 |
|---|---|---|---:|
| `腹` | `BH腹` | `{零件号}-BH腹` | 1 |
| `翼` | `BH翼` | `{零件号}-BH翼` | 2 |
| `翼-N` | `BH翼` | `{零件号}-BH翼-N` | 2 |
| `上翼` | `BH翼` | `{零件号}-BH上翼` | 1 |
| `下翼` | `BH翼` | `{零件号}-BH下翼` | 1 |
| `上翼-N` | `BH翼` | `{零件号}-BH上翼-N` | 1 |
| `下翼-N` | `BH翼` | `{零件号}-BH下翼-N` | 1 |

类型列仍只允许 `BH腹`、`BH翼`，上下翼差异由导入零件号表达。读取器角色保持稳定顺序，角色必须唯一且满足完整组合。

### 6.2 增行

- 腹板始终一行。
- 板件判同键固定为 `(规格, 宽度, 原长度, 左进, 右进, 材质)`；左进和右进是有序参数，互换后不得视为同板。
- 上下翼判同键完全相同时用一条 `翼` 行，数量为原翼数量的 2 倍；角色仅用于追溯，不阻止全参数相同板件合并。
- 上下翼不同时用 `上翼`、`下翼` 两行，各保持原翼数量。
- 多段翼板先按同一判同键稳定分组；参数相同的记录合并并累计数量，任一参数不同就各占一行。
- 增行只发生在对应 BH 源行位置，其他零件的相对顺序不变。

### 6.3 左右进和下料长度

```text
下料长度 = 长度 - 左进 - 右进
```

左进、右进采用 Reader 的 `left_safe/right_safe` 非负整数。必须满足：

- 左进和右进均有限且 `>= 0`；
- 下料长度 `> 0`；
- 同一角色没有重复记录；
- 角色数量守恒。

缺图与读取失败不能共用同一种空值语义：

- 缺图：左右进为空，`下料长度` 使用显式公式 `=M行`，表示仍按第一阶段原长度执行，并整行红色警告；
- 读取失败或规格无效：左右进为空，`下料长度` 使用条件公式 `=IF(OR(N行="",O行=""),"",M行-N行-O行)`，显示为空；人工补入左右进后可在 Excel 内继续计算；
- 正常读取：`下料长度 = 长度 - 左进 - 右进`，左右进为有序值；
- 无 BH：不创建任何 BH 深化行，其他业务行与第一阶段相同。

## 7. 公式和物理含义

第二阶段公式统一按实际下料长度计算；对读取失败占位，依赖下料长度的公式使用空值保护：

```text
P 下料长度 = M 长度 - N 左进 - O 右进
T 总数     = D 构件数 × S 数量
U 总长     = P 下料长度 × T 总数
W 理单重   = ROUND(K 规格 × L 宽度 × P 下料长度 × V 比重 / 1000000, 3)
X 理总重   = ROUND(K 规格 × L 宽度 × P 下料长度 × V 比重 / 1000000 × T 总数, 3)
```

占位行的 `U/W/X` 采用 `IF(P行="","",...)`，既保留公式痕迹，又不把未知值计算成 0。缺图行的 `P=M`，因此保持第一阶段重量口径，但其红色警告不得因公式可计算而消失。

PIP/PD 比重公式、五金手册查询和其他型材规则继续由第一阶段代码负责。非 BH 行的左右进为 0，因此第二阶段重建不会改变其数值。

### 7.1 源重量

Tekla 的单净重、总净重、单毛重、总毛重和表重量代表原模型长度，继续只保留在 BH 腹板主行；新增翼板行不复制源重量，避免成倍累计。

### 7.2 BH 聚合核验

第一阶段对 BH 的重量核验按“腹板 + 全部翼板”的完整截面聚合，不逐行拿翼板与父型材重量比较。第二阶段增加以下不变量：

- 原数量守恒：`腹数量 + 所有翼数量 = 原腹数量 + 2 × 原翼数量`；
- 左右进后理论总重不得高于相同角色的第一阶段理论总重；
- 理论重量减少量必须等于各板截面积、扣减长度、比重和总数的合计，允许的误差只来自最终三位小数显示；
- 源重量仍按原模型长度核验，不能把切短后的理论重量直接与 Tekla 原重量判为错误。
- 完整读取行参与左右进重量差核验；缺图行按第一阶段基线核验；读取失败占位不参与数值差比较，但必须计入未完成人工项，不能按 0 重量通过。

所有计算单元格同时写公式和 OOXML 缓存，并在保存后以 `data_only=False/True` 双路径回读验证。

## 8. part 重建规则

`part` 不从旧 part 复制 BH 行，而是由深化后的全量候选重新调用第一阶段 `part_builder`：

- 非 BH 候选、严重问题隔离和合并范围保持第一阶段规则；
- BH 每个深化角色都形成独立候选；
- BH 仍按构件号限定作用域；
- 相同构件、规格、宽度、原长度、有序左右进、下料长度、材质、类型、班组才允许合并；合并后的导入零件号按稳定规则生成并可回溯全部来源角色；
- 型号相同但任何参数不同都不得合并；
- `汇总` 是引用最终整理表 `总数` 的公式，并带正确缓存；
- J 列 `备注`、K 列 `文件` 保持空，L 列 `类型` 只显示六种拆板类型；
- 不新增班组信息，不从图纸或零件号臆测班组。
- 读取失败时保留第一阶段能够确定的 BH 腹/翼候选作为红色占位；规格、材质、构件和数量照常保留，下料长度及依赖结果为空，不能从 `part` 中删除。
- 失败占位的 `part.下料长度` 不是死空值，而是条件引用其全部整理表贡献行：所有贡献行的 `P` 均非空且相等时显示该值，否则保持空；人工必须补齐全部贡献行后 `part` 才自动更新。
- 第一阶段及正常第二阶段 part 的下料长度仍按现有静态值写入；条件引用只用于 Stage2 失败占位，避免改变既有工作簿合同。

第二阶段保存前必须证明：

1. 每条 part 至少对应一条整理表贡献行；
2. 每条 part 的缓存汇总等于贡献行总数之和；
3. 每个第一阶段可进入 part 的 BH 出现都被深化角色或明确的失败占位完整覆盖；
4. 非 BH part 的规范签名与第一阶段基线一致。

## 9. 报告策略

报告继续只显示核心问题和明确需要人工处理的内容：

- 正常读取、正常匹配和正常增行不写报告；
- 图纸有但 Excel 无对应零件时，按一条聚合警告列出数量和最多三个示例；
- Excel BH 缺图时部分发布，缺图行保持第一阶段长度，按核心警告列出数量和有限示例；
- Reader 失败、规格冲突、角色不完整或下料长度非法时部分发布，对应整理表和 `part` 生成红色空值占位，并列出具体图名、零件号、原因和人工动作；
- Reader 零件号重复、第一阶段基线损坏、数量不守恒或 `part` 失去可追溯关系时整批失败，不发布第二阶段正式 Excel；
- 部分发布仍生成正式左右进审计表和第二阶段 Excel；两者的 metadata、前端状态与报告统一标记 `partial`，不能显示“全部处理成功”；
- 缺图和读取失败使用 `IssueLevel.WARNING` 以保留 part，不复用会隔离零件的 `SEVERE`；writer 仅依据明确的 Stage2 人工处理类别将报告、整理表和 part 对应行着红，第一阶段原有级别、颜色和排除规则不变；
- 没有任何报告项时 `处理报告!A2` 写“无”。

前端只展示中文业务错误、失败文件数量和有限示例，不展示 Python 异常、路径、SQL 或后端日志。

## 10. Job、Artifact 和 MySQL

新增：

- `TASK_EXCEL_STAGE2 = process_excel_stage2`
- `PIPELINE_EXCEL_STAGE2 = excel_stage2`
- 专用 Celery task、execution 和 queue

完成 Job 产生两个带 `job_attempt` 的 AnalysisResult，结果状态分为 `complete`、`partial`、`noop`：

1. `workflow_artifact_type=bh_setback_excel`
2. `workflow_artifact_type=stage2_excel`

`complete` 表示全部适用 BH 已深化；`partial` 表示存在缺图或读取失败人工项；`noop` 表示项目没有 BH。三者只有在两个对象都完成 MinIO 保存、StoredFile 登记和 AnalysisResult 建立后，Job 才进入成功终态并附加正式 WorkflowArtifact。重复零件号、基线损坏或内部不变量失败仍是失败终态，只允许下载当前 attempt 的诊断表，不形成正式 Stage2 Artifact。旧 attempt 的 Artifact 可以保留审计，但所有完成检查、展示和下载只选择当前 attempt。

第二阶段公共工作簿通过现有导入器建立新的 `ExcelFinalBatch`：

- `job_id` 为第二阶段 Job；
- `file_id` 为第一阶段正式 Excel；
- `source_type=stage2_bh`；
- 整理表、构件表、质量摘要全部重新导入；
- 权限继续通过 Job 的 `project_id` 控制。

不增加新的业务表；Job、AnalysisResult、WorkflowArtifact 和 ExcelFinalBatch 已能完整表达运行、来源、结果和项目边界。

## 11. 前端交互

`excel_stage2` 从“等待上线”列表移除，使用独立组件，不继续膨胀 `WorkflowDetailPage`。

阶段卡内容：

- 第一阶段正式 Excel 名称；
- 当前分类版本和 BH 图纸数；
- Excel 中 BH 唯一零件数和出现次数；
- 预计匹配、Excel 缺图和图纸多余数量；
- 运行前检查项；
- “处理 BH 的左右进”按钮；
- 真实 Job 进度条；
- 完成后的“下载处理后的 Excel”和“下载左右进读取表”。

按钮状态：

- 预检未通过：禁用并显示具体原因；
- queued/running：禁用重复提交，显示 Job ID、attempt、处理数和总数；
- failed/cancelled：显示中文错误，允许按服务器重试规则重试；
- succeeded/complete：显示“全部 BH 已深化”，启用两个单文件下载按钮；
- succeeded/partial：显示红色“部分完成，需人工补录”，列出有限问题示例并启用两个下载按钮；
- succeeded/noop：显示“本项目无 BH，无需深化”，启用业务等价 Excel 和空审计表下载。

前端使用 Job SSE 更新，并保留 2.5 秒轮询兜底。下载复用 `TransferProgressBar`，两个 xlsx 都显示真实字节进度。

阶段完成只解锁下一阶段，不自动切换页面；用户仍停留在第二阶段核对并下载结果。

## 12. 错误代码

核心错误使用稳定代码和工人可理解的中文：

| 代码 | 前端说明 |
|---|---|
| `EXCEL_STAGE2_STAGE1_NOT_READY` | 第一阶段尚未形成正式 Excel，请先完成第一阶段。 |
| `EXCEL_STAGE2_STAGE1_BINDING_INVALID` | 第一阶段结果与当前项目或任务尝试不一致。 |
| `EXCEL_STAGE2_CLASSIFICATION_NOT_READY` | 当前项目尚无可追溯的正式分类结果。 |
| `EXCEL_STAGE2_PROJECT_MISMATCH` | 输入来源不属于当前项目，系统已阻止处理。 |
| `EXCEL_STAGE2_MANIFEST_CHANGED` | 分类清单在任务开始后发生变化，请刷新后重新提交。 |
| `EXCEL_STAGE2_BASELINE_DRIFT` | 第一阶段结果与当前规范不一致，请重新运行第一阶段。 |
| `EXCEL_STAGE2_DUPLICATE_PART_DRAWING` | 同一 BH 零件号存在多张图纸，系统未自动选取，请保留唯一正确图纸后重试。 |
| `EXCEL_STAGE2_BH_DRAWING_MISSING` | Excel 中有 BH 零件缺少对应拆板前图纸。 |
| `EXCEL_STAGE2_READER_FAILED` | 部分 BH 图纸无法可靠读取，请下载诊断表处理。 |
| `EXCEL_STAGE2_PROFILE_MISMATCH` | 图纸、分类和 Excel 的 BH 规格不一致。 |
| `EXCEL_STAGE2_ROLE_INVALID` | BH 腹板或翼板组合不完整，不能生成下料数据。 |
| `EXCEL_STAGE2_CUT_LENGTH_INVALID` | 左右进导致下料长度无效，请核对图纸。 |
| `EXCEL_STAGE2_QUANTITY_UNBALANCED` | BH 增行后数量不守恒，系统已停止发布。 |
| `EXCEL_STAGE2_PART_INCONSISTENT` | part 与整理表无法一一核对，系统已停止发布。 |
| `EXCEL_STAGE2_RESULT_NOT_READY` | 第二阶段尚未形成可下载的正式 Excel。 |

## 13. 已完成的样本推演

### workflow 5

- 分类 BH：112 张；全部 Reader `OK`
- Excel BH：110 个唯一零件、117 个构件内出现
- 图纸多余：`3t1-cb-136`、`4t1-cb-73`
- Excel 缺图：0
- 整理表：2346 → 2360
- part：1109 → 1123
- 数量守恒失败：0
- 最短下料长度：400 mm
- BH 理论总重变化：`-844.8061476 kg`

### workflow 6

- 分类 BH：111 张；全部 Reader `OK`
- Excel BH：109 个唯一零件、117 个构件内出现
- 图纸多余：`3b1-cb-134`、`4b1-cb-72`
- Excel 缺图：0
- 整理表：2333 → 2347
- part：1098 → 1112
- 数量守恒失败：0
- 最短下料长度：400 mm
- BH 理论总重变化：约 `-877.871 kg`

两个项目都需要新增 14 行，来自 14 个出现中上翼和下翼不同；不是数量丢失。两个项目的多余图纸名称不同，也证明匹配和警告必须严格按项目计算，不能复用跨项目缓存。

## 14. 与现有功能的兼容合同

新增能力采用“新任务、新队列、新产物、复用稳定内核”的边界，不能借 Stage2 改写现有功能语义。

| 现有能力 | 必须保持的行为 | Stage2 的接入方式 |
|---|---|---|
| 独立 Excel 第一阶段 `/files/excel-final` | 上传、预检、处理、单文件下载和数据库入库合同不变 | 公共 writer 默认仍使用模型长度；Stage2 显式选择下料长度策略 |
| 工作流 Excel 第一阶段 | 仍只产生一个 `stage1_excel`，现有按钮、错误和下载路径不变 | 抽取公共冻结源表核验，但保留原 API 响应字段和 Job task |
| DXF 分类 | 分类结果、分组下载、类型统计和现有分类版本不变 | 只新增按显式 Run ID 查询 BH 的只读接口，并消除文件查询 N+1 |
| BH/BOX 拆板 | 任务、重试次数、正式结果和余料交接不变 | Stage2 不调用 splitter，不读取任何拆板后文件 |
| 工作流生命周期 | 阶段完成后只解锁、不自动切换；旧 attempt 不补当前结果 | 将当前 attempt 过滤泛化到所有 Job-backed stage，并对已有阶段做回归 |
| `part_builder` | 非 BH 合并、构件作用域、参数冲突和严重问题隔离不变 | Stage2 只生成新的 BH PartCandidate，再调用同一 builder |
| 质量级别与颜色 | Stage1 warning/severe 的计数、颜色和 part 隔离语义不变 | Stage2 人工处理项保持 warning，仅按明确类别额外着红并保留占位 |
| 五金手册与 PIP/PD | 唯一 MySQL 手册、材质路由和公式计算规则不变 | Stage2 重建复用第一阶段投影，不增加第二套查询规则 |
| MySQL 数据控制台 | 现有 Stage1 批次、零件、构件和权限可继续查询 | Stage2 新建独立 ExcelFinalBatch，不覆盖 Stage1 batch |
| MinIO 与下载 | 原名、对象登记、transfer、字节进度和项目权限不变 | 使用相同事务接缝，新增两个明确的 xlsx 下载端点 |
| Celery worker | Stage1、分类和拆板队列不被长任务阻塞 | 新建 `excel_stage2` 专用队列和 worker |
| 保护镜像 | 现有受保护功能完整、源码不进入最终层 | Reader 作为锁定 path dependency 编译进同一 protected runtime |
| 5000 张上传 | 浏览器前 5000 张、后端上限和红色警告不变 | Stage2 消费冻结后的分类账本，不重新定义上传上限 |

兼容性验证必须包含三类比较：

1. **第一阶段不变：** 相同源表在重构前后的公开工作簿、公式缓存、报告摘要和数据库投影一致。
2. **非 BH 不变：** Stage2 最终 Excel 中所有非 BH 整理行和 part 规范签名与第一阶段一致。
3. **旁路不变：** 分类、拆板、余料、独立 Excel 页面、数据控制台和现有下载 E2E 全部继续通过。
4. **降级可辨：** `complete/partial/noop/failed` 在 Job、Artifact metadata、前端和工作簿报告中含义一致，任何空值都不能被当成 0 或成功结果。

任何兼容回归都必须修复后再继续，不能以“Stage2 已能运行”替代现有能力验证。

## 15. 发布验收

正式完成必须同时满足：

1. 第一阶段原有测试和十份监督样本回归无变化。
2. Reader 1.2.7 的 80 项测试和 199 张真实图双后端结果不回退。
3. workflow 5/6 真实端到端结果符合本规格的行数、part 数、缺图和多余图纸结论。
4. 缺图、Reader 失败、无 BH 分别得到 `partial`、`partial`、`noop`；红色占位、条件公式、part 引用和下载均符合本规格。
5. 同项目重复 BH 零件号整批失败；全参数判同和有序左右进规则通过回归。
6. 两项目同名图纸并发测试无交叉数据。
7. 5000 条分类清单测试无大 Job JSON、无 N+1 查询、无超量进度写入。
8. 5000 张压力任务的内存和临时磁盘峰值有界，取消后无残留工作目录。
9. 两个 xlsx 下载均是单文件、原名合理、字节进度可见。
10. 受保护镜像包含 Reader 和第二阶段代码的可执行字节码，无源码遗漏导致的加密后功能丢失。
11. `scripts/verify.sh full`、前后端测试、Compose 合同和保护镜像 smoke 全部通过。
