# 数据库基础设施

## 现有内容

`mysql/` 保存 MySQL 容器初始化、运行参数和硬件/部署核对说明；Compose 通过 `mysql` service、持久 volume、健康检查和应用/迁移凭据接入。应用 schema 由 `backend/migrations/` 的 17 个 Alembic revision 创建。

## 输入、输出与边界

输入是 MySQL 8.x、`.env.docker` 凭据、volume 和资源参数，输出是 FastAPI/Celery 共用的数据库实例。手写 SQL 只用于实例初始化/诊断，不能替代 Alembic 或直接新增业务表。
