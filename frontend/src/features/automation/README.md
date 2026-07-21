# 自动化与 Agent 合同

## 现有实现

`api.ts` 调用 Agent run/memory 与 capability 合同端点，`types.ts` 描述 run、step、session memory 和 disabled/not-implemented 状态，`index.ts` 只暴露公共请求与类型。目前没有独立可执行 Agent 页面。

## 业务流与边界

输入是后端 automation 的已持久化只读/会话接口，输出供后续界面使用的真实状态。LLM、LangGraph、MCP、ZWCAD 和 Agent Celery task 均未实现；前端不得通过按钮或成功提示伪装启用。
