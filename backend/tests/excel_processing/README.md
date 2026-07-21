# Excel 处理测试

## 现有覆盖

adapter/import/models/retry/idempotency 测试验证 Stage 子进程、流式 workbook 导入、batch/part/component 三表、Files/Job 关联、attempt 清理、重复请求和 14 个 route；`test_excel_final_idempotency_mysql.py` 专门覆盖真实 MySQL 并发语义。

## 证据边界

输入是 Excel fixture、隔离存储和 SQLite/MySQL 条件环境，输出是既有单文件 Excel Final 闭环证据；MySQL 条件未满足时对应项跳过，且不证明全图纸最终聚合已实现。
