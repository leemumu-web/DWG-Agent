# 数据库设计落地

当前模型已覆盖规范中的核心表：

- `sys_users`
- `sys_roles`
- `sys_permissions`
- `sys_user_roles`
- `sys_role_permissions`
- `projects`
- `project_members`
- `files`
- `drawings`
- `drawing_versions`
- `jobs`
- `job_steps`
- `agent_runs`
- `agent_run_steps`
- `analysis_results`
- `review_records`
- `audit_logs`

本机与 Docker 运行数据库均以 MySQL 为目标；本机使用 `127.0.0.1:3306`，Docker 使用服务名 `mysql`。
pytest 的内存 SQLite 仅作为隔离 test double，不代表运行数据库口径。

初始化命令：

```bash
cd backend
uv run python -m app.db.init_db
```
