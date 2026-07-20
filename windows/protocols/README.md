# Linux 与 Windows 通信协议

Status: draft interface only; transport and authentication are not implemented.

目标 envelope 至少包含 `protocol_version`、`message_id`、`node_id`、`command_id`、`workflow_id`、`stage_code`、`attempt`、`lease_id`、`fencing_token`、`issued_at`、`expires_at`、`idempotency_key`、artifact 引用与 SHA-256。结果必须区分 accepted/running/succeeded/failed/cancelled，并携带可公开错误码与脱敏诊断引用。

接口不得把 bearer secret 放进 URL、日志或产物；重试不得跨 attempt 覆盖新任务；过期 lease 或旧 fencing token 必须拒绝。协议实现、签名、mTLS/服务凭据、重放保护与兼容性测试均留待后续交付。
