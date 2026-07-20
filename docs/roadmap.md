# 路线图

## 基线

截至 2026-07-20 的实现，本仓库是实际 React/FastAPI/MySQL/Celery/storage 平台，不只是骨架。当前已有 authentication/RBAC、project/file/Job/result/review/audit/workflow model、114 个 OpenAPI path / 135 个 operation、attempt-safe Job execution、Local/MinIO adapter、文件流转账本、一致性扫描与处置、非破坏式每日归档、七页签数据控制台、Steel DXF Classifier 1.1.0 分类分流、Excel Final 关系化导入，以及含“多 DWG + 单 Excel → 服务器 DXF → 冻结 → 分类分流”的 Linux 生产流程界面。

这不表示每个目录都 production-ready。feature flag 默认关闭，Linux 工作流只真实接通 DXF→Excel 和 Excel Final；拆板、CAM 工作包、Windows/SinoCAM 和结果接纳仍是显式留白。Compose 缺少完整运维自动化；大规模验证 corpus 不随仓库分发。

Redis/Valkey 已从活动运行时架构完整移除。历史 migration 或说明只能把它作为已移除历史提及。

## P0：仓库可复现性

`Stages/dxf2excel` 已在 2026-07-18 从不可还原 gitlink 转为父仓库普通跟踪目录，源码、锁文件和内置单测进入统一门禁。剩余完成标准：

- 在外部临时目录完成全新 clone、`uv sync --locked`、全部 Stage/backend/frontend 测试和 backend/frontend image build；
- 为 419 文件历史验证 corpus 建立许可合规、摘要固定且不进入 Git 的获取流程；
- 确认 ODA、dxf2excel 源码、第三方依赖和样本数据的 license/provenance；
- 项目负责人选择并发布仓库 LICENSE，明确内部源码与不可再分发资产的边界。

## P0：求实的 HTTP/TLS 部署

当前已选择明确的短期状态：Compose 删除无效 `443:8443` 映射，只发布 `${HTTP_PORT:-80}:8080`，并将 HTTP-only 作为可信内网风险接受，而不是 HTTPS 能力。

若未来需要公网入口，完成标准是实现受控 TLS termination、证书/私钥只读管理、HTTP redirect、验证后 HSTS、renewal/expiry 检查和 secure-cookie browser 测试。必须提供真实 TLS handshake 以及经 HTTPS 的 refresh/SSE/download 证据；单个端口映射仍不是证据。

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

## P1：生产工作流深化

DXF→Excel、Excel Final、attempt 同步、文件/结果产物和 active Job 取消已经接线。下一阶段：

- 为拆板、CAM 工作包、Windows/SinoCAM 与结果接纳实现当前已暴露的接口契约；
- 将复核批准/退回与 design barrier 和失败恢复接通；
- 增加并发推进的行锁/version 控制；
- 持久化带 SHA-256、算法版本和 attempt 的确定性交付清单；
- 使用真实 MySQL/Celery/MinIO/Nginx 和许可样本验证完整 Linux 链路。

## 冻结 / 排除子系统

保持 `AGENT_ENABLED=false` 和 `CAD_WORKER_ENABLED=false`。Agent/model/MCP 执行、CAD 构件提取/分类、自动或交互拆板、左右进分析、中望 CAD 二次开发和 Windows CAD Worker 明确不在当前项目范围。现有 route、model、配置符号和占位目录可以为兼容保留，但不得将其描述为计划交付。

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

每项交付同时修改相关中文文档、生成 API 参考、根状态矩阵和组件 README。声明必须说明代码、默认 flag、依赖、验证环境/日期和剩余限制。

`make docs-check` 必须拒绝 stale API output、broken link、重新出现的旧双语镜像、过时本地端口、错误 schema/head/TLS 声明和缺失已知仓库 blocker。

## 明确非目标

- Redis/Valkey 作为 session、authorization、progress、Pub/Sub、broker、result 或 fail-open store。
- 进程内状态用于掩盖 MySQL、broker 或 storage failure。
- 实现或启用 Agent/model/MCP 执行、CAD 图纸业务算法、交互拆板或 Windows CAD Worker。
- 把 mocked/SQLite 测试当作 MySQL/MinIO/Celery 生产行为证据。
- 把 HTTP Compose、不协调 backup、未复验外部 corpus 或仅有 clean source checkout 当作 production readiness。
- 没有分阶段 migration 证据、会破坏 buildable/testable vertical path 的大重写。
