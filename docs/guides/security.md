# 安全

## 信任边界

Nginx 和 React 不是授权边界。所有业务 route 在 FastAPI 认证，并执行全局角色加资源/项目检查。Compose 把 FastAPI、MySQL、MinIO 放在 internal network，但当前浏览器到 Nginx 的 Compose 流量是 HTTP，不是 TLS。

威胁模型包括不可信文件名、文件内容、ZIP 结构、query/body 字段、过期或类型混淆 token、未授权项目访问、stale Celery message、被攻陷浏览器 JavaScript、child-process error 和依赖中断。它不声称能抵抗完全被攻陷的应用主机、数据库管理员、MinIO 管理员或签名 secret。

## 认证

- 密码使用推荐 Argon2id 实现；错误密码和不存在用户路径都执行 hash verification，降低 timing enumeration。
- Access JWT 默认 30 分钟；refresh cookie 默认 14 天。
- 强制 access/refresh token `type`，不能互换。
- 登出把可用 token JTI 存入 MySQL `token_blacklist`。
- 改密写入 `password_changed_at`；拒绝更早的 access/refresh token。
- 每个认证请求重新读取用户，并拒绝 disabled/deleted 用户。
- Refresh 为 HttpOnly、SameSite=Lax、path `/api/v1/auth`；SSE cookie 为 HttpOnly、SameSite=Lax、path `/api/v1/jobs`。

Secure cookie 默认值跟随 `APP_ENV=production`。公网部署需要真实 TLS 和 Secure cookie。设置 `REFRESH_COOKIE_SECURE=false` 只是私有 HTTP 网络的显式风险接受；当前 Compose 只发布 HTTP 且不发布 443。

## 浏览器 Token 边界

前端把 access token 和用户快照放在 `sessionStorage`，把持久化限制在一个 tab/session。这不是 XSS 防护：同源脚本执行可以读取 access token 并调用 API。CSP、依赖审查、输出编码、禁止 unsafe HTML 和较短 access 生命周期仍然必要。

Axios interceptor 把并发 401 refresh 合并为一个请求，并对每个原请求重试一次。它永远不会递归重试 login 或 refresh。尚未实现 refresh-token rotation：refresh 返回新 access token，但保留现有 refresh cookie 直到过期/登出/改密。

## SSE 认证

原生 EventSource 不能发送 Bearer header。登录/刷新设置短期 `dwg_sse_token` HttpOnly cookie。SSE dependency 只接受该 cookie，验证 access-token type/revocation/user state，并在 streaming 前检查 Job access。query string 不接受 token。重连提供当前 MySQL 快照，不回放历史 event。

## 授权

全局角色为 `super_admin/admin/engineer/reviewer/operator/viewer/auditor`。项目成员角色为 owner/engineer/reviewer/viewer。管理员全局访问是明确规则；其他全局角色不能绕过项目/资源校验。

文件读取要求以下之一：

- administrator-level 全局项目访问；
- 上传者 ownership；
- 是通过 drawing version 或 analysis result 关联的 active project member。

文件删除仅限上传者或管理员。文件列表和 batch metadata 在 SQL 中应用访问过滤。结果、result download path 和复核继承父 Job 边界；无项目 Job 仅管理员或创建者可读。即使 Agent 执行仍关闭，Agent run 详情/步骤也使用 creator/admin/linked-project 检查。

数据控制台使用独立的管理边界：`super_admin/admin/auditor` 可以读取总览、文件登记、对象清单、流转流水、扫描和异常；只有 `super_admin/admin` 可以启动扫描和执行处置。处置必须先生成绑定当前操作人、动作、finding 集合、实时摘要、数量、字节上限和五分钟有效期的签名预检 token，再使用幂等键执行。服务端会锁定相关行并重新检查对象状态；前端隐藏按钮不构成授权。

## 任务完整性

重试创建新 attempt。claim、progress、completion、failure、cancellation、dispatch compensation 和 stale recovery 都包含 status/attempt 条件。`job_steps.attempt` 保留历史，且不允许 stale worker 覆盖更新世代。

Celery JSON serialization 受 allowlist；启用 late ack 和 lost-worker reject。MySQL SQL transport 与应用数据库不是独立安全边界：能修改 broker 和 Job table 的账号可以破坏队列完整性。因此数据库凭据和授权至关重要。

## 文件与 ZIP 安全

- 文件名规范化移除路径穿越、控制字符、分隔符和危险前缀。
- 扩展名 allowlist，但仍需内容特定校验。
- DWG 要求受支持 AC header 和最小大小。
- 上传流式限制字节并计算 SHA-256/MD5。
- ZIP 限制 entry 数和总解压字节，并拒绝路径穿越。
- Storage key 由服务端生成，不把用户 path 当作可信输入。
- DXF 预览拒绝超限源文件/实体/输出，禁用外部图像，并在返回前拒绝 script、foreignObject、href、DOCTYPE 和 ENTITY；SVG 只通过鉴权内容端点返回，带私有缓存、`nosniff` 和限制性 CSP。
- 数据库 rollback best-effort 删除 commit 前写入的对象。
- 永久清理只接受同一扫描中的 `untracked_object` finding，要求显式确认词；对象删除后元数据提交失败会留下 `compensation_required` 流水，不能伪装成可回滚的单库事务。

`MAX_ZIP_EXTRACT_MB` 和 `MAX_ZIP_ENTRY_COUNT` 有代码默认值，尽管两份环境模板没有同时暴露活动行。受审计部署应显式设置。

## 下载

签名 path 本身不是 bearer capability。下载端点仍要求当前 Bearer access token 和文件权限，并验证 file ID/expiry 上的 HMAC。签名在 300 秒后过期。

前端单文件只在网络、403、408、429 或 5xx 失败后，用新签名进行第二次尝试。其他 4xx 不重试。ZIP 下载使用认证 POST body，需要自己的 timeout/error handling。

## 错误与密钥处理

未处理异常只在服务端记录。`DEBUG=false` 时客户端收到通用 500 envelope。Child-process stderr、traceback、DSN、secret 和 host path 禁止进入 `jobs.error_message` 或响应。开发 `DEBUG=true` 可能暴露异常字符串，禁止从不可信网络访问。

Excel Final child password 通过环境而非命令行传递。环境变量仍需要 host/process 隔离，禁止 dump 到诊断信息。

## 数据库与基础设施

- 构造 DSN 时 URL-encode MySQL password。
- 应用/Celery pool 有界、pre-ping 并 recycle。
- Celery 使用 `READ COMMITTED` 和 queue-scoped index 降低锁范围。
- Compose 不发布 MySQL 或 MinIO 宿主端口。
- Compose 初始化中的手册库访问只有 `SELECT`。
- Nginx 限制 login/API rate、每 IP connection 和 request size，并添加浏览器安全 header。

Nginx 当前没有 TLS server、证书处理或 HSTS，Compose 也不发布 443。rate limit 和 header 可以降低暴露面，但不能让不可信网络上的明文登录安全。

## 审计边界

应用通过 `write_audit_log` 记录认证、用户/角色、项目/成员、文件、Job、复核、Agent-run 和 Excel Final action。API policy 把审计读取限制为 `super_admin` 和 `auditor`。

该表只是应用约定上的 append-only：没有 API update/delete route 使用它。它**不是密码学不可变，也没有数据库强制 append-only**。row 包含 `updated_at`，且没有 trigger、signature chain、WORM sink、独立 audit credential 或外部 SIEM。高权限数据库或应用泄露可以修改记录。高保证部署需要外部 append-only export 和独立访问控制。

## 生产检查表

- 替换每个模板/默认 credential，并使用支持轮换的 secret manager。
- 公网暴露前实现并验证 TLS；保持 Secure cookie。
- 限制 CORS/origin、network ingress、database grant 和 object-store policy。
- 构建未审查本地内容前修复 `Stages/dxf2excel` provenance。
- 验证 ODA license，并 sandbox 不可信 CAD/Excel 处理。
- 协调加密 MySQL/object backup 并测试恢复。
- 增加集中日志、metrics/alert、dependency/component scanning 和 incident retention。
- 运行 permission、attempt-race、upload/ZIP、signed-download、debug-error、storage-outage 和真实 browser 测试。

## 剩余风险

- 同源 XSS 下 access token 暴露。
- 没有 refresh-token rotation 或 device/session inventory。
- 没有 malware scanning、file quarantine、sandbox boundary 或 content disarm。
- ODA/Excel 子进程在 worker host 上处理不可信复杂文件。
- SQL broker 与数据库共享安全和故障域。
- 审计不可防篡改。
- 当前 HTTP 部署会在不可信网络暴露 credential/token。
- 没有自动 secret rotation、backup、monitoring、retention 或 security update SLA。
