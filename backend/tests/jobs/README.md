# 任务领域测试

## 现有覆盖

生命周期、attempt、claim 和 access 测试覆盖创建、幂等、投递补偿、锁、fencing、取消、重试与项目权限；module contract 覆盖 route/task；adversarial 覆盖越权/非法状态；`test_job_events_mysql.py` 在可用 MySQL 上验证 SSE/并发事实。

## 证据边界

输入是隔离数据库、同步 Celery fixture 与可选 MySQL，输出是 Job/Step/Result/Review 状态机证据；当前 SSE 只提供最新快照，没有事件历史 replay。
