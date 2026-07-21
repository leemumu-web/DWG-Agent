# 消息与 Worker 基础

## 现有实现

`celery_app.py` 创建官方入口 `app.platform.messaging.celery_app:celery_app`，显式加载 7 个真实 task module、注册 11 个稳定公共任务名，并为 10 个队列族保留确定路由。`agent`、`cad`、`dispatch` 的路由只是未来消息的 transport seam，没有注册 task；其余 7 个路由族承载当前任务。该文件还初始化 Kombu SQL 表/索引、清理有界 runtime 结果、发布 Worker 就绪信号并支持 bootstrap 注册恢复 callback。

## 业务运行

输入是 MySQL SQLAlchemy broker/result URL、队列和 JSON task，输出是 8 组本地 worker 可消费的异步传输与运行记录。公共 `app.workers.tasks_*` 只作为已发布消息名保留。

## 未完成边界

当前没有 RabbitMQ fanout/remote-control、Beat 或事务 Outbox；SQL transport 在整机崩溃后的恢复依赖 Job stale reconciliation，不能宣传为等价 RabbitMQ。`docs/architecture/runtime-contract.json` 同时锁定任务名与 `pattern -> queue`，但路由存在不代表该队列已有执行器。
