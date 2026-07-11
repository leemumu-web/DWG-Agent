Alembic migration directory.

运行数据库以 MySQL 为准。初始化入口使用 `bash scripts/db.sh init`，该命令先执行
`alembic upgrade head` 建立/升级 schema，再执行 `app.db.init_db` 写入幂等种子数据。

迁移链验证入口为 `bash scripts/db.sh migration-test`：创建临时 MySQL schema，从空库执行
`alembic upgrade head`，校验业务表、`alembic_version` 和 TimestampMixin 时间列后清理临时库。
