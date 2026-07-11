# 安全

> 英文对应文档：[../security.md](../security.md)

## 信任边界

Nginx 和前端不是授权边界。所有业务端点都在 FastAPI 认证，并执行全局角色和资源/项目访问检查。Compose 中 MySQL 和对象存储位于私有网络。

## 认证

- 密码使用 Argon2id。
- access JWT 默认 30 分钟，refresh cookie 默认 14 天。
- access/refresh token 类型严格检查，不能互换。
- 登出把 token JTI 写入 MySQL `token_blacklist`。
- 密码变更写 `password_changed_at`，旧 token 被拒绝。
- 每个认证请求都检查用户是否 disabled/deleted。
- 前端把 access 状态放在 `sessionStorage`，减少跨 tab 持久化。
- refresh cookie 为 HttpOnly、SameSite，生产默认 Secure；仅私有 HTTP VPN 可显式覆盖。

吊销机制不存在 fail-open：token 状态在权威 MySQL 中，不依赖可选缓存。

## SSE 认证

原生 EventSource 不能设置 Bearer header。API 下发短期 HttpOnly `dwg_sse_token` cookie，由 SSE dependency 校验。事件流查询参数不接受 token，开始 streaming 前执行普通 Job 权限检查。

## 授权

全局角色：`super_admin/admin/engineer/reviewer/operator/viewer/auditor`。

项目资源要求成员身份和允许的项目角色。管理员全局角色有明确的全局项目访问，其他角色没有。普通 admin 不能禁用、删除、重置或管理 super-admin 目标角色。

文件读取要求满足以下之一：

- 管理员全局访问；
- 上传者所有权；
- 是通过图纸版本或分析结果关联的活跃项目成员。

文件列表和 batch metadata 使用同一 SQL 权限过滤。batch 端点不能泄露不可访问文件的元数据。结果详情、结果下载 URL 和复核委托给父 Job 权限；无项目 Job 仅管理员和创建者可访问。Agent run 启用后，详情和步骤都要求创建者/管理员/关联项目访问。

## 任务完整性

每次重试创建新 `attempt`。领取、进度、终态、取消、投递补偿和 stale 恢复都使用包含 status 和 attempt 的条件更新，防止旧 worker 覆盖重试。

`job_steps.attempt` 保留历史且不混合世代。cancel-all 锁定精确 active ID，只修改这些行，并按队列报告 broker purge 结果。

## 文件安全

- 文件名规范化移除路径穿越、控制字符、分隔符和危险前缀。
- 扩展名白名单。
- DWG 要求支持的 AC header 和最小大小。
- 上传流式限制大小并计算 SHA-256/MD5。
- ZIP 限制 entry 数和总解压字节，拒绝路径穿越。
- 对象 key 由系统生成，不使用用户路径。
- 数据库 rollback 补偿 commit 前写入的对象。

## 下载

签名 URL 本身不足以授权。下载端点还要求 Bearer 认证和当前文件权限。HMAC 绑定 file ID 和 expires。前端重试会获取新签名，不重放过期 URL。

## 错误处理

未处理异常只在服务端记录。生产响应使用稳定错误码和通用消息。子进程 stderr、traceback、DSN、secret 和主机路径不得进入客户端可见的 `jobs.error_message`。

Excel Final parser 错误映射为有界公共消息，完整 child traceback 只在 worker log。

## 数据库与 Broker

- 构造 DSN 时 URL 编码 MySQL 凭据。
- 应用连接池有界并回收。
- Celery 使用 `READ COMMITTED` 和队列顺序索引降低锁范围。
- 禁用 SQL transport fanout control。
- Compose 不发布 MySQL/MinIO 宿主端口。
- 手册库仅授予 `SELECT`。

## 审计

登录/登出、用户生命周期、角色、项目/成员、文件上传/下载/删除、任务生命周期、复核和敏感操作写不可变审计行。审计仅 `super_admin` 和 `auditor` 可读。

## 生产清单

- 替换全部 `CHANGE_ME_*`、JWT secret、管理员密码、MySQL 和 MinIO secret。
- 使用 TLS；公网保持 secure refresh cookie。
- Nginx origin 和 CORS 只允许部署前端。
- MySQL/MinIO 保持私网并保护卷备份。
- 发布前运行迁移和安全边界测试。
- 验证 `/health/ready` 不暴露凭据或内部异常。
- 疑似泄露后轮换凭据并使 session 失效。
- 审计日志和存储完整性 hash 定期检查。

## 安全测试

回归覆盖 token confusion、禁用用户、super-admin 保护、项目隔离、无项目结果隔离、文件所有权/成员关系、签名过期/篡改、batch metadata、文件列表固定查询数、Agent run 隔离、attempt 竞态、存储补偿和安全错误消息。
