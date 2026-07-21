# Agent 持久化模型

## 现有实现

`runs.py` 定义 AgentRun/AgentStep 的用户、状态、顺序、输入输出与时间事实；`memory.py` 定义按用户/session 隔离、可过期的 AgentMemory；`__init__.py` 只导出模型。三张表已进入 model registry/Alembic 现有 schema。

## 输入、输出与边界

输入是认证用户的 run/step/memory 数据与数据库 session，输出是可审计持久化事实。模型存在不代表 LLM、LangGraph、MCP 或 CAD 执行器已经实现。
