# Excel 处理测试

## 现有覆盖

`test_excel_final_adapter.py` 验证 Stage 路径、依赖探针、隔离子进程、安全错误和旧定宽 bolt 行；`test_excel_final_import.py` 验证流式 sheet/header/稀疏行导入；`test_excel_final_models.py` 锁定 batch/part/component 三表类型、关系与长度；`test_excel_final_retry.py` 验证 attempt 替换、临时行清理和单 session 失败结算；`test_excel_final_idempotency.py` 验证 Files/Job 复用、同键冲突和真实 backend 健康投影。`test_excel_final_idempotency_mysql.py` 专门覆盖真实 MySQL 并发语义，其他 route 测试锁定 14 个 operation。

## 证据边界

输入是 Excel fixture、隔离存储和 SQLite/MySQL 条件环境，输出是既有单文件 Excel Final 闭环证据；MySQL 条件未满足时对应项跳过，且不证明全图纸最终聚合已实现。
