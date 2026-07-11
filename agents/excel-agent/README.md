# Excel Agent / Excel 智能体

## English

Reserved directory for a future conversational Excel Agent. It is not used by the current Excel Final workflow.

Current Excel Final is deterministic: FastAPI validates and creates a Job, Celery calls `Stages/excel_final` in an isolated child process, MySQL stores Job/results/parts/components, and Local/MinIO stores workbooks. `EXCEL_FINAL_PIPELINE_ENABLED` controls that workflow; `AGENT_ENABLED` does not.

Do not describe this directory as an implemented feature. Any future Agent must reuse platform authorization, storage, attempt, audit, and safe-error boundaries rather than importing the Stage directly into an API request.

## 中文

这是未来对话式 Excel Agent 的预留目录，当前 Excel Final 工作流不使用它。

当前 Excel Final 是确定性流程：FastAPI 校验并创建 Job，Celery 在隔离子进程调用 `Stages/excel_final`，MySQL 保存 Job/result/part/component，Local/MinIO 保存工作簿。该工作流由 `EXCEL_FINAL_PIPELINE_ENABLED` 控制，与 `AGENT_ENABLED` 无关。

禁止把此目录描述为已实现功能。未来 Agent 必须复用平台 authorization、storage、attempt、audit 和 safe-error 边界，不能在 API 请求中直接导入 Stage。
