# 五金手册数据库工具

本目录只维护由唯一可信 `/home/Creeken/Paper/CAD_research/五金手册.xls` 生成和审计 `hardware_handbook` 的入口。

- `sync_hardware_handbook.py` 读取源工作簿，生成包含逐行来源外键、确定性业务值和冲突保留语义的 MySQL SQL；不从旧数据库或代码常量补数据。
- `audit_hardware_handbook.py` 逐表、逐行、逐值比较已部署数据库与源工作簿，并拒绝多余表、缺失行、改写值或来源断链。

正式替换数据库前应先保留可恢复备份；替换后必须运行审计并得到 `problem_count=0`。

## 边界

这里不维护手册业务值、人工修订表或第二套查询库；运行时只查询经上述唯一源生成并通过逐值审计的正式库。
