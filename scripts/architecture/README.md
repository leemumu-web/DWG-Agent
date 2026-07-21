# 架构检查脚本

## 现有实现

`snapshot_contracts.py` 从 FastAPI、ORM、Celery、React router、Compose 和 Alembic 生成/比较运行快照；`check_module_catalog.py` 验证 owner 路径、排序、唯一表/operation/task 与能力状态；`check_partition_docs.py` 发现真实源码分区并要求就地业务 README。

## 输入、输出与边界

输入是可导入后端、仓库源码和架构 JSON，输出是确定性非零/通过门禁。普通重构只能 `--check`，不得用 `--write` 覆盖意外合同漂移。
