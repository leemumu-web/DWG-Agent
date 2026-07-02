# Agent 子系统占位说明

当前阶段不实现 Agent 内部逻辑，只保留平台边界：

- `POST /api/v1/agent-runs`
- `GET /api/v1/agent-runs/{agent_run_id}`
- `GET /api/v1/agent-runs/{agent_run_id}/steps`
- `GET /api/v1/agent-tools`

默认 `.env`：

```text
AGENT_ENABLED=false
```

因此接口会返回 `503 Service Unavailable`。后续接入 LangGraph、LLM、MCP、Redis session memory 时，不需要修改前端资源模型。
