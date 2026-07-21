# 自动化测试

## 现有覆盖

`test_agent_memory.py` 验证 Agent run/step/session memory 三张表、用户隔离、容量/清理、API 读写和 capability disabled/not-implemented 响应，同时确认缺失 executor 不会因配置开关变成可执行。

## 证据边界

输入是隔离数据库与认证 fixture，输出是已交付账本/会话切片可靠且占位不伪成功的证据；不执行 LLM、LangGraph、MCP、ZWCAD 或 Agent Celery task。
