# Agent 产品边界

## 当前分区

`cad-agent/`、`excel-agent/`、`report-agent/` 分别说明三个历史产品名在当前平台中的真实状态。它们没有可执行源码、任务注册或模型调用；平台侧已交付的 run/step/session-memory 表与禁用 API 统一归 `backend/app/modules/automation/agent/`，机器可读 capability 归 `automation/contracts/`。

## 进入与输出规则

未来 Agent 产品必须复用 identity/projects/files/jobs 的授权、attempt、存储和审计边界，并提供真实模型/MCP 执行、超时、恢复、幂等、安全与验收证据。当前目录输出仅是产品说明，不参与 Python import、Celery registry 或 Compose 启动。

## 未实现边界

目录或 `AGENT_ENABLED` 配置存在不能代表 Agent 可用；禁止增加一行空 task/client 伪装实现，也禁止在 FastAPI 请求线程直接执行长时模型/CAD 操作。
