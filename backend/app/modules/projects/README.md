# Projects domain

本模块拥有项目、项目成员、图纸目录和图纸版本。`interface.py` 是 files、jobs、workflows、classification 等领域访问项目范围和图纸对象的唯一稳定入口。

## 目录与责任

| 路径 | 责任 |
|---|---|
| `interface.py` | 项目/图纸模型和成员授权函数的公共边界；不装载 HTTP routers |
| `access.py` | 全局管理员旁路、active project、成员与项目角色检查 |
| `routes/projects.py` | `/projects` 列表、创建、更新和成员管理 |
| `routes/drawings.py` | `/drawings` 列表、版本、归档与现有 preview contract |
| `services/` | 项目成员和图纸版本事务操作 |
| `models/` | `projects`、`project_members`、`drawings`、`drawing_versions` ORM |
| `schemas/` | 项目、成员、图纸和版本 HTTP contracts |

## 不变量与依赖

- 非 admin/super-admin 用户只能读取其 `project_members` 可见范围；列表过滤在 SQL 分页前完成。
- 项目写入角色与项目 owner 操作继续使用现有角色集合；HTTP path、payload 和错误码保持不变。
- 图纸版本号由事务内当前最大版本递增；`Drawing.current_version_id` 与版本行保持现有更新顺序。
- 全局角色判断只通过 `app.modules.identity.interface`；对象字节、文件登记和预览渲染不归本模块。
- 审计写入通过 operations audit interface；MySQL Session/分页属于 platform。

主要回归位于 `backend/tests/projects/`，跨领域历史审计位于
`backend/tests/regression/`；领域所有权由
`backend/tests/architecture/test_identity_projects_boundaries.py` 锁定。
