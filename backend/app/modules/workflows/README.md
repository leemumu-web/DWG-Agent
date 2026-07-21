# Production workflows module

本模块拥有项目级生产批次、十阶段流程、输入冻结和阶段产物引用。公开 HTTP 前缀保持为
`/api/v1/workflows`；五张表、16 个 operation、错误码、审计 action 和 Job 幂等键均保持
不变。本模块不另建文件存储或任务队列。

## 已确定输入契约

操作员只上传一批多个 DWG 和恰好一个可读 XLS/XLSX，不上传 DXF。`intake/registration.py`
重新读取 Local/MinIO 对象，核对 SQL 登记的大小与 SHA-256，并验证 DWG 文件头或 Excel
工作表；`intake/conversion.py` 为每个 DWG 幂等建立 `convert_dwg_to_dxf` Job，只接受当前
attempt 的成功 Result 和可读同名 DXF；`intake/freeze.py` 再次校验对象和配对，建立 Drawing、
artifact 与 canonical manifest SHA-256。冻结后 DWG、Excel 和派生 DXF 均不能从 `/files`
旁路删除。

结构图中早期的“DXF + DWG + Excel 上传”文字已被用户随后确认的上述规则取代。代码、API
描述和测试均以服务器派生 DXF 为准；保留旧文字不构成允许人工 DXF 的兼容承诺。

## 内部职责

- `models/orchestration.py`：`workflow_runs`、`workflow_stage_runs`、`workflow_artifacts`。
- `models/intake.py`：`workflow_input_batches`、`workflow_input_items`。
- `templates.py`：三个模板和阶段能力的唯一事实源；未实现阶段保持 placeholder/external。
- `lifecycle.py`：创建、启动、人工交接、取消和整体状态重算。
- `artifacts.py`：阶段类型白名单和 file/result 引用幂等绑定。
- `job_sync.py`：`job_id + attempt` 绑定、Result 投影和阶段推进。
- `stage_execution.py`：已实现阶段的参数/权限/feature flag/Job 复用计划；不提交或投递。
- `intake/`：按登记、转换、冻结、展示四种状态转换拆分。
- `routes/`：只处理 HTTP dependency、项目授权、审计、commit 后 dispatch 和 envelope。
- `interface.py`：其他业务模块唯一允许导入的工作流边界。

## 依赖方向与事务边界

- 文件行、对象字节和传输补偿仍归 `files`；工作流只保存 `file_id`。
- Job、Step、Result、attempt 和 Celery 投递仍归 `jobs`；工作流只绑定当前 attempt。
- Drawing/Version 仍归 `projects`；冻结用例在同一数据库事务中组合它们。
- 分类 run/item 和 Classifier 1.1.0 仍归 `dxf_classification`；分类通过工作流公开接口读取
  已验证对象并挂接 artifact。
- `files` 删除保护通过延迟调用 `workflows.interface.find_frozen_input_reference` 获取不可变
  标识，避免 files/workflows 两个公开接口在 import 时形成环。
- bootstrap 可直接装配 `models` 和 `routes/router.py`；其他业务模块不得导入内部文件。

## 当前真实边界

`source_intake`、`dxf_classification`、`excel_stage1` 和 `excel_final` 已接入现有服务器实现，
但仍受 feature flag、worker、Stage、MySQL、对象存储和真实样本约束。`drawing_processing`、
`cam_packaging`、`windows_cam` 与 `result_acceptance` 只有稳定输入、产物和 501/人工交接契约；
自动拆板、CAM 打包、Windows Node Agent/SinoCAM 和结果接纳算法尚未实现。目录整理不能被
解释为生产闭环已经完成。

## 验证

行为回归位于 `backend/tests/workflows/`，分类集成位于
`backend/tests/dxf_classification/`；结构边界位于
`backend/tests/architecture/test_workflow_boundaries.py`。运行时快照继续锁定 114 path、
135 operation、36 张模型表和 11 个 Celery task。
