# Identity domain

本模块是会话、用户和全局 RBAC 的唯一业务归属。对外调用只使用 `interface.py`；模块内部文件可以互相引用，其他业务模块不得穿透到 `routes/`、`models/` 或 `schemas/`。

## 目录与责任

| 路径 | 责任 |
|---|---|
| `interface.py` | 跨领域稳定入口：当前用户依赖、角色判断和身份模型；不装载 HTTP routers |
| `dependencies.py` | Bearer/SSE cookie 当前用户依赖与原始 access token 提取 |
| `authentication.py` | 凭据校验、access/refresh token 构造、密码变更失效、token blacklist |
| `access.py` | `require_roles`、全局角色集合和 admin 判断 |
| `users.py` | 用户创建、资料更新、状态迁移和密码重置 |
| `routes/` | `/auth`、`/users`、`/roles`、`/permissions` HTTP 适配、cookie scope 与 router 组合 |
| `models/` | 用户、角色、权限、关联表与 token blacklist ORM |
| `schemas/` | 身份领域请求/响应 Pydantic contracts |

## 拥有的数据

`sys_users`、`sys_roles`、`sys_permissions`、`sys_user_roles`、`sys_role_permissions`、`token_blacklist` 六张表。幂等初始角色、权限和管理员数据由 `app.bootstrap.seed` 装配，因为 platform 层不得反向导入本模块。

## 边界

- 密码/JWT 算法原语属于 `app.platform.security`；本模块决定 token 类型、吊销和密码变更失效语义。
- 数据库 Session 属于 `app.platform.database`，FastAPI 的通用 DB dependency 属于 `app.platform.http`。
- 项目成员和项目角色属于 `app.modules.projects`，不在全局 RBAC 中伪造。
- 审计行通过 `app.modules.operations.audit.interface` 写入；身份模块不拥有审计表。
- UI 权限守卫只改善交互，最终授权始终由这里的 FastAPI dependency 和领域规则决定。

主要回归位于 `backend/tests/identity/` 和 `backend/tests/security/`；领域所有权由
`backend/tests/architecture/test_identity_projects_boundaries.py` 锁定。
