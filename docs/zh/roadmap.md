# 路线图

> 英文对应文档：[../roadmap.md](../roadmap.md)

## 基线

截至 2026-07-11 对 `main@d178fcf` 的审计，本仓库是实际 React/FastAPI/MySQL/Celery/storage 平台，不只是骨架。它有 authentication/RBAC、project/file/Job/result/review/audit model、71 个 OpenAPI path、attempt-safe Job execution、Local/MinIO adapter、四条转换 service path、Excel Final 关系化导入和广泛自动测试。

这不表示每个目录都 production-ready。feature flag 默认关闭，Agent/CAD task 是占位，Compose 缺少 TLS/运维自动化，`Stages/dxf2excel` 也无法从 clean clone 重建。

Redis/Valkey 已从活动运行时架构完整移除。历史 migration 或说明只能把它作为已移除历史提及。

## P0：仓库可复现性

首先修复 `Stages/dxf2excel` 归属，因为它影响 backend dependency resolution 和 Docker build。

完成标准：

- 选择普通跟踪目录，或恢复有效 `.gitmodules` metadata；
- pin 可获取且已审查的 commit/source；
- 移除对当前已填充但未被跟踪 nested working tree 的依赖；
- 通过全新 clone、`uv sync --locked`、Stage 测试、backend 测试和 Docker build；
- 记录 source license/provenance，并更新 Stage component README。

## P0：求实的 HTTP/TLS 部署

选择一种明确短期状态：

- 删除无效 `443:8443` 映射，并记录 HTTP-only 私网使用；或
- 实现 container `8443 ssl`、只读证书挂载、HTTP redirect、验证后 HSTS、renewal/expiry 检查和 secure-cookie browser 测试。

完成必须有真实 TLS handshake 和经 HTTPS 的 refresh/SSE/download flow。单个 Compose port mapping 不是证据。

## P0：可靠性门禁

- 保持 MySQL 为业务事实，阻止 process-memory fallback。
- 每个 Job 状态写入保持 status + attempt 条件。
- 派生 result 和 batch metadata 保持权限检查。
- 保持对象 rollback compensation，并增加周期对象/数据库 reconciliation。
- 测试 clean migration、受支持 downgrade 和代表性 populated upgrade。
- task、storage、auth 或 download 变化后运行真实 broker/storage/browser 工作流。

## P1：运维基线

必须实现，而不只是写文档：

- 协调 MySQL + MinIO backup，并支持 encryption/retention；
- 调度 restore test，保留 checksum 证据并测量 RPO/RTO；
- API latency/error、DB pool、queue depth/age、Job duration/failure、storage 和 worker health metric；
- 集中 structured log 和 request/Job/attempt correlation；
- 可操作 alert、dashboard、runbook link 和 incident retention；
- capacity test 和明确 connection/worker/object limit；
- 保持数据库/对象一致性的 retention/deletion job。

## P1：处理加固

- 建立有代表性且许可合规的 DWG/DXF/Excel corpus，带预期输出和失败分类。
- sandbox 或隔离不可信 ODA/Excel 处理；增加 CPU、memory、disk、process 和 output limit。
- 在每个 result 中定义 Stage version metadata，并定义算法变化的 migration behavior。
- 在复杂文件处理前增加 malware scanning/quarantine。
- 验证 cancellation 在安全时终止 child work，不只更新 Job status。
- 为部分外部失败增加确定性 object reconciliation 和 retry policy。

## P1：Agent 子系统

以下标准全部通过前保持 `AGENT_ENABLED=false`：

- 用有界、可取消 task 替换 `tasks_agent.py` 占位；
- 实现 model/MCP client timeout、retry、tool allowlist、payload/result validation 和 secret isolation；
- 只持久化有界 memory 和安全 step summary；禁止暴露隐藏 reasoning 或 tool secret；
- 对 run、step、source/output file 强制 creator/admin/project access；
- 审计 model/tool selection 与结果 artifact，但不记录敏感 payload；
- 覆盖 prompt/tool injection、未授权 tool call、stale attempt、cancellation、dependency outage 和真实 E2E。

## P1：Windows CAD Worker

以下标准全部通过前保持 `CAD_WORKER_ENABLED=false`：

- 定义认证且抗 replay 的 worker registration/dispatch protocol；
- 实现 Celery CAD task 和真实 Windows service，不只是 `CAD_WORKER_API_BASE`；
- 按 Job attempt 实现幂等 dispatch，并强制 timeout/cancellation；
- 安全传输 source/result artifact 并验证 SHA-256；
- 把 worker error 映射为安全稳定 code，并保留 server-side diagnostic；
- 增加 Compose/external-service topology、health、upgrade 和真实 ZWCAD sample 测试。

## P2：Broker 与扩展决策

用真实 queue count、Job duration、worker concurrency、API load 和 failure recovery 压测当前 MySQL SQL transport。记录 connection consumption 和 broker table growth。需求超出时通过 ADR 采用 RabbitMQ 或其他合适 broker，同时让 Job/progress/result authorization 留在 MySQL。

不要恢复 Redis 作为业务状态源。未来 cache 需要显式 consistency model，并且永远不能授权或决定 Job truth。

## P2：身份与安全成熟度

- Refresh-token rotation、session/device inventory、强制 session revocation 和 key rotation。
- 外部 identity/SSO 与 privileged role MFA。
- 使用独立 credential 和 retention 的 tamper-evident audit export。
- Dependency/container/SBOM scanning 和 patch SLA。
- CSP 收紧、XSS-focused test 和安全 file preview isolation。
- 在运维合理时，按 API、worker、migration、broker 和 audit 需求拆分 least-privilege database user。

## P2：用户体验

- 针对 keyboard、focus、label、contrast、table、dialog、progress 和 error 的 accessibility audit。
- 清晰 offline/reconnecting/expired-session/storage-outage state。
- Large-list 和 large-file performance test。
- 每条 pipeline 一致的 retry/cancel/download 行为。
- operator 可见 attempt history，且不把 stale step 与 active attempt 混淆。

## 文档验收

每项交付同时修改相关英文/中文 pair、生成 API 参考、根状态矩阵和 component README。声明必须说明代码、默认 flag、依赖、验证环境/日期和剩余限制。

`make docs-check` 必须拒绝 stale API output、broken link、pair-structure drift、过时本地端口、当前分支 `codex` 引用、错误 TLS 声明和缺失已知仓库 blocker。

## 明确非目标

- Redis/Valkey 作为 session、authorization、progress、Pub/Sub、broker、result 或 fail-open store。
- 进程内状态用于掩盖 MySQL、broker 或 storage failure。
- 为演示启用未通过安全门禁的占位 Agent/CAD feature。
- 把 mocked/SQLite 测试当作 MySQL/MinIO/Celery 生产行为证据。
- 把 HTTP Compose、不协调 backup 或已填充本地 gitlink 当作 production readiness。
- 没有分阶段 migration 证据、会破坏 buildable/testable vertical path 的大重写。
