# 存储维护脚本

## 现有实现

`verify_transactions.py` 在隔离 Local/MinIO + MySQL 环境验证上传、预览、鉴权出库、软删除和 transfer 账本；`reap.py` 按年龄/状态/引用扫描临时对象并默认 dry-run，通常由 `scripts/db.sh reap-storage` 调用。

## 输入、输出与边界

输入是数据库/对象存储连接、保留期和显式 include-orphans/执行确认，输出是探针报告或有界回收结果。不得猜测 object key、跳过登记或对生产执行无预览删除。
