# 文件领域测试

## 现有覆盖

`test_file_service.py`、`test_adversarial_files.py` 覆盖格式头、名称、上传、分页、权限、软删除与路径攻击；`test_file_transfer_models.py`、`test_file_transfer_service.py` 覆盖入库/出库/失效 saga；`test_storage_inventory.py`、`test_storage_consistency.py`、`test_storage_adversarial.py` 覆盖 Local/MinIO 列举、扫描、hash、orphan/missing 和安全处置；`test_streaming_zip.py` 验证 ZIP 头先于对象读取产生、中文路径和叶子文件名保持不变。

## 证据边界

输入是隔离数据库、临时 Local 或模拟 MinIO，输出是 MySQL 文件事实与对象流转规则证据；生产 MinIO 凭据、容量和恢复演练仍需基础设施验收。
