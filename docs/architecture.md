# 架构落地说明

本阶段将规范中的生产架构收敛为本机开发架构：

```text
React + Vite
  ↓ HTTP
FastAPI /api/v1
  ↓ SQLAlchemy
SQLite 本地开发库
  ↓ local storage adapter
backend/var/storage
```

保留的生产边界：

```text
MySQL    -> DATABASE_URL 可切换
MinIO    -> storage adapter 可替换
Redis    -> 配置占位
Celery   -> workers 目录占位
Agent    -> agent-runs / agent-tools API 占位
DXF      -> tasks_dxf.py 占位
ZWCAD    -> cad-worker 协议文档占位
Docker   -> infra 目录占位，当前不启动
```

核心分层：

- `api/v1`：RESTful 路由层。
- `schemas`：Pydantic 请求/响应模型。
- `services`：业务流程。
- `repositories`：后续复杂查询封装。
- `models`：SQLAlchemy ORM。
- `storage`：文件存储抽象。
- `workers`：异步任务占位。
- `agents`：Agent 工厂和工具注册占位。
- `integrations`：外部 CAD Worker 客户端占位。
