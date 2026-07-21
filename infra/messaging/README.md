# 消息基础设施

## 现有内容

`rabbitmq/` 保存目标 exchange/queue/dead-letter、凭据与部署说明；它没有进入当前 Compose 服务集合，也没有被 `celery_app.py` 选为 broker。当前 8 组 worker 实际使用 MySQL Kombu SQLAlchemy transport。

## 输入、输出与未完成边界

未来输入需要 RabbitMQ service、TLS/凭据、拓扑声明、投递/恢复迁移和故障验收，输出才可成为生产 broker。现有文档/目录只是目标合同，不能显示为已运行。
