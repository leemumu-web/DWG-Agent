# CAD Agent / CAD 智能体

## English

Reserved directory for a future LangGraph/MCP CAD Agent. It contains no executable platform implementation. `backend/app/workers/tasks_agent.py` is a placeholder, and `AGENT_ENABLED=false` must remain the default.

Existing Agent API/models provide only an interface and persistence boundary. Delivery still requires a real bounded Celery task, model/MCP clients, tool allowlist, timeouts/cancellation, prompt/tool-injection controls, secret isolation, authorization/audit, and real end-to-end tests. See [roadmap](../../docs/roadmap.md) and [security](../../docs/security.md).

Do not add local memory, fake success, or direct tool execution here to bypass MySQL Job/permission rules.

## 中文

这是未来 LangGraph/MCP CAD Agent 的预留目录，没有可执行平台实现。`backend/app/workers/tasks_agent.py` 是占位，`AGENT_ENABLED=false` 必须保持默认值。

现有 Agent API/model 只提供接口和持久化边界。交付仍需真实有界 Celery task、model/MCP client、tool allowlist、timeout/cancellation、prompt/tool-injection 控制、secret isolation、authorization/audit 和真实端到端测试。见[路线图](../../docs/zh/roadmap.md)和[安全](../../docs/zh/security.md)。

禁止在此增加 local memory、fake success 或直接 tool execution 来绕过 MySQL Job/permission 规则。
