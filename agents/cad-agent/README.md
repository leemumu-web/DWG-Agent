# CAD Agent 占位目录

本目录没有可执行平台实现。`backend/app/workers/tasks_agent.py` 是占位，`AGENT_ENABLED=false` 必须保持默认值。现有 Agent route/model 只保留接口和持久化边界，不代表 LangGraph、模型、MCP 工具或 CAD 操作已经接通。

Agent/model/MCP 执行不是当前项目交付目标。禁止在此添加进程内 memory、伪成功或绕过 MySQL Job/项目权限的直接工具调用。保留代码仍需遵守[安全边界](../../docs/guides/security.md)。
