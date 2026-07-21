# 可观测性基础

## 现有实现

`logging.py` 根据配置初始化统一日志级别、时间、logger 和消息格式，供 FastAPI、Celery 与脚本诊断关联使用。

## 输入与输出

输入是日志级别、Python logging record 和运行上下文，输出是一致的进程日志；HTTP request ID 由 HTTP/bootstrap 链路附加。

## 边界

本区没有指标采集、持久事件或告警发送；监控部署归 `infra/operations/monitoring`，业务审计归 operations/audit。
