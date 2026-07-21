# 数据库基础

## 现有实现

`base.py` 提供 declarative Base；`session.py` 创建 engine、SessionLocal 和请求 session；`mixins.py` 提供 UTC timestamp 列；`pagination.py` 提供有界 offset/limit 与 count/list 帮助器。

## 输入与输出

输入是已验证数据库 DSN、连接池配置和 SQLAlchemy statement，输出是统一事务、连接生命周期与分页结果。

## 边界

36 张业务表、owner 查询和 commit/rollback 决策归领域模块；Alembic 是 schema 权威，平台层不得反向导入 `app.modules`。
