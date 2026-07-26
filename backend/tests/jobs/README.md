# 任务领域测试

## 现有覆盖

`test_job_lifecycle.py` 覆盖基础状态转换、step、cancel/retry 与跨项目校验；`test_job_attempts.py` 锁定新执行世代、stale worker fencing、投递补偿、SSE 当前 attempt 和 Excel 临时行清理；`test_job_claim.py` 验证并发 claim 只有一个胜者；`test_job_access.py` 验证无项目 Job、Result/Excel 查询和他人上传隔离；`test_jobs_module_contract.py` 验证公开 lifecycle 接口；`test_adversarial_jobs.py` 覆盖越权、非法状态、排序白名单和分页边界；`test_job_events_mysql.py` 在可用 MySQL 上验证 SSE/并发事实；`test_job_diagnostics.py` 验证任务诊断只输出白名单业务字段且不泄漏日志、路径或 worker 信息。

## 证据边界

输入是隔离数据库、同步 Celery fixture 与可选 MySQL，输出是 Job/Step/Result/Review 状态机证据；当前 SSE 只提供最新快照，没有事件历史 replay。
