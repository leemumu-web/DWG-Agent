# Production workflows module

本模块拥有项目级生产批次、十阶段流程、输入冻结和阶段产物引用。公开 HTTP 前缀保持为
`/api/v1/workflows`；本模块拥有七张表、公开 operation、错误码、审计 action 和 Job
幂等键。本模块不另建文件存储或队列，整批清理复用现有 `maintenance` worker。

## 已确定输入契约

操作员分步上传一个可读 `.xls`/`.xlsx` 单文件和一个含多个 DWG 的文件夹，不上传 DXF。
DWG 文件夹混有其他文件时，浏览器确认后只发送 DWG；服务端 DWG 入口仍拒绝任何非 DWG。
`intake/registration.py`
重新读取 Local/MinIO 对象，核对 SQL 登记的大小与 SHA-256，并通过 Excel Final 阶段一的
版本化输入规则检查表格；`intake/conversion.py` 为每个 DWG 幂等建立 `convert_dwg_to_dxf`
Job，只接受当前 attempt 的成功 Result 和可读同名 DXF；`intake/freeze.py` 再次校验对象和
配对，建立 Drawing、artifact 与 canonical manifest SHA-256。冻结后的 `DrawingVersion`
指向服务器生成的 `canonical_dxf`；源 DWG 只作为 `source_dwg` 留档。冻结后 DWG、Excel
和派生 DXF 均不能从 `/files` 旁路删除。

结构图中早期的“DXF + DWG + Excel 上传”文字已被用户随后确认的上述规则取代。代码、API
描述和测试均以服务器派生 DXF 为准；保留旧文字不构成允许人工 DXF 的兼容承诺。

## 内部职责

- `models/orchestration.py`：`workflow_runs`、`workflow_stage_runs`、`workflow_artifacts`。
- `models/intake.py`：`workflow_input_batches`、`workflow_input_items`。
- `models/exports.py`：`workflow_batch_exports` 保存四类短期导出；
  `workflow_retention_exports` 保存整条 Workflow 的完整备份清单、下载凭据摘要、异步清理和
  补偿状态。
- `templates.py`：三个模板和阶段能力的唯一事实源；未实现阶段保持 placeholder/external。
- `contracts.py`：阶段输入/输出类型、文件归属和 DXF 对象结构的统一门禁。
- `lifecycle.py`：创建、唯一生产流程约束、启动、人工交接、取消和整体状态重算。
- `production_projects.py`：原子组合 Project 创建、唯一生产 Workflow 创建与启动。
- `artifacts.py`：阶段类型白名单和 file/result 引用幂等绑定。
- `job_sync.py`：`job_id + attempt` 绑定、Result 投影和阶段推进。
- `stage_execution.py`：已实现阶段的参数/权限/feature flag/Job 复用计划；Excel 预检与正式
  执行复用同一冻结输入门，预检不创建 Job。
- `access.py`：集中 workflow detail 加载、项目成员授权和 route 共享常量。
- `intake/`：按登记、转换、冻结、展示四种状态转换拆分。
- `routes/`：只处理 HTTP dependency、项目授权、审计、commit 后 dispatch 和 envelope。
- `routes/archive.py`：复用 Files ZIP、传输登记和审计能力，提供完整流程及普通阶段压缩包；
  `excel_stage1` 只下载经来源链校验的唯一 `.xlsx`，其阶段 ZIP 入口返回稳定错误。
- `routes/batch_exports.py`：在不落服务器临时 ZIP 的前提下流式导出四类文件；只有出库
  流水成功且用户再次确认后，才物理删除所选对象及其 DXF 预览缓存。
- `retention.py`、`retention_tasks.py` 与 `routes/retention.py`：从输入、全部 attempt 的
  Result、分类、拆板、人工决定和 artifact 一次收齐完整范围；逐对象校验后流式备份，只有
  完整下载成功、管理员输入精确确认词后才由维护队列整批清理。中断保留数据库关系和可重试
  补偿流水。
- `interface.py`：其他业务模块唯一允许导入的工作流边界。

## 依赖方向与事务边界

- 文件行、对象字节和传输补偿仍归 `files`；工作流只保存 `file_id`。
- 分批清理物理删除 Local/MinIO 字节并将 `files` 行标记为 `deleted + purged_at` 墓碑，
  以保留外键生产链；墓碑不再进入 reaper，且对应 `workflow_artifacts` 文件引用被删除。
- Job、Step、Result、attempt 和 Celery 投递仍归 `jobs`；工作流只绑定当前 attempt。
- Drawing/Version 仍归 `projects`；冻结用例在同一数据库事务中组合它们。
- 分类 run/item 和 Classifier 1.2.0 仍归 `dxf_classification`；分类通过工作流公开接口读取
  已验证对象并挂接 artifact。
- `files` 删除保护通过延迟调用 `workflows.interface.find_frozen_input_reference` 获取不可变
  标识，避免 files/workflows 两个公开接口在 import 时形成环。
- bootstrap 可直接装配 `models` 和 `routes/router.py`；其他业务模块不得导入内部文件。

## 当前真实边界

`source_intake`、`dxf_classification` 和 `excel_stage1` 已接入现有服务器实现；`excel_stage1`
从冻结清单解析唯一 `source_excel`，不接收浏览器提供的文件 ID 或 DXF 批次名，底层复用现有
Excel Job。运行前核对冻结清单、唯一源表、对象摘要、Excel 合同和正式拆板交接，正式执行
再次复用同一门禁。DXF→Excel 仅保留为独立工具，不属于生产主流程。上述阶段仍受 feature flag、worker、
Stage、MySQL、对象存储和真实样本约束。新建流程使用 `definition_revision 4`，历史流程保留原 revision；图纸链固定为
`source_dwg → canonical_dxf → classified_dxf → processed_dxf → cam_input_dxf →
cam_output_dxf → accepted_dxf → delivery_dxf`；Excel、报告和清单保持各自格式。
`drawing_processing` 已接入 Steel DXF Split 1.5.2：从当前分类 attempt 冻结整批输入，
通过专用 worker 执行 BH/BOX 拆板和独立校验，在 MySQL 登记 run/item/复核决定，在 MinIO
登记正常图、余量增长图、报告、批次清单和 `BH拆板信息表.xlsx`。每批只执行一个
attempt；每 30 张和尾批分别核对输入数、完成数与业务分流数，单图问题直接保留明确诊断。
前端展示真实进度、速度、剩余时间和生产结果数量，只提供正式拆板 DXF 与本批原图两个
ZIP 入口，不展示候选、报告或逐图人工复核工作台。
若分类结果中没有任何可拆的 BH/BOX 图纸，流程不会创建空的拆板 Job，也不会锁住后续阶段。
系统将 `drawing_processing` 明确标记为“无需处理”，保存 `no_split_candidates` 原因，
并直接把 Excel 第一阶段置为可输入；分类结果和原图清单仍然保留，便于追溯。
Stage A3 的“图纸拆板与独立校验”卡片标题栏提供“分批导出”：`原 DXF`、
`正常拆板 DXF`、`原 Excel`、`产出 Excel` 分别映射当前 attempt 的
`classified_dxf`、`processed_dxf`、冻结 `source_excel` 与成功 `stage1_excel`。
ZIP 一级目录固定为 `原DXF/`、`正常拆板DXF/`、`原Excel/`、`产出Excel/`，目录内
严格保留数据库登记的原文件名；重名冲突直接拒绝，不做自动改名。
同一标题栏的独立“导出”按 `failed_bh`、`failed_box`、`pl`、`other` 四个机器类别
筛选当前 run 未自动接纳的分类 DXF，通过短期路径能力直接从对象存储流式生成 ZIP；
它不落服务器临时文件、不改叶子文件名，也不执行物理删除。

`excel_stage2` 已接入独立、并发受限的 `excel_stage2` worker：只消费当前成功 attempt 的
`stage1_excel`，以及分类账中冻结的拆板前 BH `classified_dxf`，不读取拆板产物。预检与
正式执行都核对工作流、项目、Job/attempt、对象摘要和 BH 清单来源；worker 先登记
`bh_setback_excel`，再登记正式 `stage2_excel`，二者仅通过独立 xlsx 下载端点交付。没有
BH 图纸时仍生成保留第一阶段内容的正式结果；读图或匹配阻断时只允许下载当前 attempt 的诊断读取表，绝不回退旧批次。CAM 打包、Windows Node Agent/SinoCAM 和结果接纳算法尚未实现，目录整理不能被解释为这些后续阶段已形成生产闭环。
Workflow 列表在服务端聚合 Project 编号/名称，并返回忽略状态筛选、但遵守项目权限与
Workflow 类型范围的全局状态统计，避免前端用独立分页做不完整关联。

## 验证

行为回归位于 `backend/tests/workflows/`，分类集成位于
`backend/tests/dxf_classification/`；结构边界位于
`backend/tests/architecture/test_workflow_boundaries.py`。运行时快照锁定 178 path、
206 operation、47 张模型表、15 个 Celery task 和 13 条任务路由。
