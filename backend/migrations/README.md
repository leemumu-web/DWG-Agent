# Alembic Migrations / Alembic 迁移

## English

Alembic owns the 22 application business tables. It deliberately excludes eight Celery/Kombu runtime tables and does not own the `hardware_handbook` dataset. Current head is `a74c2e9f1d30`.

```bash
# From repository root
bash scripts/db.sh init
bash scripts/db.sh migration-test

# From backend/
uv run alembic current
uv run alembic check
```

`migration-test` creates a temporary empty MySQL schema, upgrades to head, verifies critical tables/columns/types/FKs/indexes, and removes it. It does not test downgrade or migration time on production-sized data. The `drawings`/`drawing_versions` circular FK produces a known table-sort warning; review generated operations despite that warning.

## 中文

Alembic 管理 22 张应用业务表，有意排除 8 张 Celery/Kombu runtime table，也不管理 `hardware_handbook` 数据集。当前 head 为 `a74c2e9f1d30`。

```bash
# 仓库根目录
bash scripts/db.sh init
bash scripts/db.sh migration-test

# backend/ 目录
uv run alembic current
uv run alembic check
```

`migration-test` 创建临时空 MySQL schema，升级到 head，验证关键 table/column/type/FK/index 后删除。它不测试 downgrade，也不测生产数据量迁移耗时。`drawings`/`drawing_versions` 循环 FK 会产生已知 table-sort warning；即使有该 warning 也必须检查生成 operation。
