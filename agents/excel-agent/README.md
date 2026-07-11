# Excel Agent 占位目录

本目录未被当前 Excel Final 使用，也没有对话式 Agent 实现。Excel Final 是确定性管线：FastAPI 校验并创建 Job，Celery 在隔离子进程调用 `Stages/excel_final`，MySQL 保存 Job/result/part/component，Local/MinIO 保存工作簿；它由 `EXCEL_FINAL_PIPELINE_ENABLED` 控制，与 `AGENT_ENABLED` 无关。

禁止把本目录描述为已实现功能，也禁止在 API 请求线程直接导入 Stage。平台调用必须复用授权、存储、attempt、审计和安全错误边界。
