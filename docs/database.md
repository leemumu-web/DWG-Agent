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

本机开发默认使用 SQLite。生产切换 MySQL 时保持 SQLAlchemy 模型不变，修改 `DATABASE_URL` 即可。

初始化命令：

```bash
cd backend
uv run python -m app.db.init_db
```
