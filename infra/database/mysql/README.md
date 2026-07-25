# MySQL 初始化

Status: implemented for first-time Compose volume initialization.

- `init.sql` 建立平台应用用户对 `dwg_agent` 的必要授权，并授予五金手册只读权限。
- `hardware_handbook.sql` 初始化外部参考数据的仓库内基线。
- 应用 schema 由 Alembic 管理；修改这里不会升级已有 `mysql_data` volume。
- Celery SQL broker/result 表由 Kombu/Celery 按需创建，不属于 Alembic 模型迁移。

Compose 依次只读挂载为 `01-platform.sql` 与 `02-hardware-handbook.sql`。已有环境必须通过迁移或受控数据导入更新，不能删除 volume 以“应用”脚本变化。

## 五金手册唯一来源

`/home/Creeken/Paper/CAD_research/五金手册.xls` 是手册数据的唯一可信导入源。仓库内
`hardware_handbook.sql` 不是第二份人工维护的数据源，而是由该工作簿确定性生成的部署产物。
生成器固定校验源文件 SHA-256；源文件未经复核发生变化时拒绝生成。

数据库保存一条 `source_workbook`、2025 条非空 `source_row` 原始记录和 1967 条一一对应的
物理语义记录。每条语义记录以 `source_row_id` 外键追溯到源 Sheet 和行号；标题、表头、
备注等未映射行仍保存在 `source_row.raw_values`。源内重复行不覆盖，重复键重量冲突由
运行时返回 `conflict`，不得 `LIMIT 1` 擅自选值。库中不再存在派生的 `material_lookup`。

在 `backend/` 环境生成和逐值审计：

```bash
uv run python ../scripts/database/sync_hardware_handbook.py \
  /home/Creeken/Paper/CAD_research/五金手册.xls \
  --output-sql ../infra/database/mysql/hardware_handbook.sql

uv run python ../scripts/database/audit_hardware_handbook.py \
  /home/Creeken/Paper/CAD_research/五金手册.xls
```

审计同时检查表集合、各表行数、源身份、全部原始行 JSON 和全部语义字段；只有
`problem_count=0` 才表示运行库与源文件逐值一致。应用账号只授予
`hardware_handbook.*` 的 `SELECT`，Excel Final 不查询其他手册库或静态重量表。
