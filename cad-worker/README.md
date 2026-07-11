# Windows ZWCAD Worker / Windows ZWCAD 工作节点

## English

Protocol placeholder only. There is no Windows service, executable, registration/authentication protocol, artifact transfer, Celery CAD task, health endpoint, installer, Compose service, or end-to-end test in this directory.

`CAD_WORKER_API_BASE`, `CAD_WORKER_API_KEY`, `CAD_WORKER_ENABLED`, queue name `cad`, and `tasks_cad.py` are future interface symbols, not a delivered worker. Keep the flag false.

Completion requires authenticated replay-resistant dispatch, idempotency by Job attempt, timeout/cancellation, secure source/result transfer with SHA-256, safe error mapping, Windows lifecycle/upgrade documentation, and tests against a real supported CAD application. See the [roadmap](../docs/roadmap.md).

## 中文

这里只是协议占位。目录中没有 Windows service、executable、registration/authentication protocol、artifact transfer、Celery CAD task、health endpoint、installer、Compose service 或端到端测试。

`CAD_WORKER_API_BASE`、`CAD_WORKER_API_KEY`、`CAD_WORKER_ENABLED`、队列名 `cad` 和 `tasks_cad.py` 是未来接口符号，不是已交付 worker。对应 flag 必须保持 false。

完成需要认证且抗 replay 的 dispatch、按 Job attempt 幂等、timeout/cancellation、带 SHA-256 的安全 source/result transfer、安全 error mapping、Windows lifecycle/upgrade 文档和真实受支持 CAD 应用测试。见[路线图](../docs/zh/roadmap.md)。
