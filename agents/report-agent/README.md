# Report Agent / 报告智能体

## English

Reserved directory for a future report Agent. The current `report` Celery queue executes `run_stub_job`, a framework smoke/result path used to verify API -> MySQL broker -> worker -> MySQL/storage behavior. It is not an LLM report generator and does not use this directory.

The Compose core starts `worker-report` because the stub is useful for infrastructure verification. Its healthy state proves Celery startup only. Keep `AGENT_ENABLED=false` until the Agent completion criteria in the [roadmap](../../docs/roadmap.md) pass.

## 中文

这是未来报告 Agent 的预留目录。当前 `report` Celery 队列执行 `run_stub_job`，用于验证 API -> MySQL broker -> worker -> MySQL/storage 的 framework smoke/result 路径。它不是 LLM 报告生成器，也不使用本目录。

Compose core 默认启动 `worker-report`，因为该 stub 可用于基础设施验证。其健康只证明 Celery 启动。通过[路线图](../../docs/zh/roadmap.md)中的 Agent 完成标准前保持 `AGENT_ENABLED=false`。
