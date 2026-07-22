# Excel 处理测试

## 现有覆盖

`test_excel_final_adapter.py` 验证 Stage 路径、v1 隔离子进程协议、安全错误、单表输入和分类手册查询；`test_excel_final_import.py` 验证规范/旧版工作簿的流式 sheet/header/稀疏行导入；`test_excel_final_quality.py` 验证报告摘要、独立计数交叉核验和表净重/表毛重汇总；`test_excel_final_models.py` 锁定 batch/part/component 三表类型、关系与长度；`test_excel_final_retry.py` 验证 attempt 替换、临时行清理、单 session 失败结算和成功质量传播；`test_excel_final_idempotency.py` 验证 Files/Job 复用、同键冲突、lookup HTTP 合同和真实 backend 健康投影。`test_excel_final_idempotency_mysql.py` 专门覆盖真实 MySQL 并发语义。

## 证据边界

输入是 Excel fixture、隔离存储和 SQLite/MySQL 条件环境，输出是既有单文件 Excel Final 闭环证据；MySQL 条件未满足时对应项跳过，且不证明全图纸最终聚合已实现。
