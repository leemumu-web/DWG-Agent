# Alembic 迁移

Alembic 当前管理 47 张应用模型表，唯一 head 为 `c9a1d4e7f620`。该 revision 为恢复后的账号增加强制重置密码状态，阻止管理员在设置新密码前启用账号。Alembic 有意排除 Celery/Kombu 按需创建的 8 张 broker/result runtime 表，也不管理 `hardware_handbook` 参考数据；该库只由唯一可信 `/home/Creeken/Paper/CAD_research/五金手册.xls` 确定性生成并单独审计。

```bash
# 仓库根目录：初始化和空 MySQL 迁移验证
bash scripts/db.sh init
bash scripts/db.sh migration-test

# backend/：查看和校验迁移
uv run alembic current
uv run alembic check
```

`migration-test` 创建临时空 MySQL schema，升级到 head，验证模型表、Job 请求幂等约束和关键列/FK/index 后删除。它不测试 downgrade、已填充生产数据升级时间或 Celery runtime 建表。`drawings`/`drawing_versions` 循环 FK 会产生已知 table-sort warning；仍必须审查是否出现新的 autogenerate operation。详见[数据库参考](../../docs/reference/database.md)。
