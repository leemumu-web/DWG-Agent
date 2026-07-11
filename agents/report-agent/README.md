# Report Agent 占位目录

本目录没有 LLM 报告生成器。当前 `report` Celery 队列执行 `run_stub_job`，用于验证 API -> MySQL broker -> worker -> MySQL/result/storage 的框架路径，并不使用本目录。

Compose 核心默认启动 `worker-report`，因为 stub 有助于基础设施验证。worker healthy 只证明 Celery 进程 ready，不证明 Agent 能力。`AGENT_ENABLED` 必须保持 false，Agent 执行也不是当前项目交付目标。
