# MySQL 初始化

Status: implemented for first-time Compose volume initialization.

- `init.sql` 建立平台应用用户对 `dwg_agent` 的必要授权，并授予五金手册只读权限。
- `hardware_handbook.sql` 初始化外部参考数据的仓库内基线。
- 应用 schema 由 Alembic 管理；修改这里不会升级已有 `mysql_data` volume。
- Celery SQL broker/result 表由 Kombu/Celery 按需创建，不属于 Alembic 模型迁移。

Compose 依次只读挂载为 `01-platform.sql` 与 `02-hardware-handbook.sql`。已有环境必须通过迁移或受控数据导入更新，不能删除 volume 以“应用”脚本变化。
