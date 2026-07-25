# FastAPI 应用源码

## 现有实现

`main.py` 只保留稳定 ASGI 入口 `app.main:app`；`bootstrap/` 组合路由、模型、任务、生命周期和 seed；`platform/` 提供数据库、HTTP、消息、存储等技术机制；`modules/` 保存业务 owner；`integrations/` 只保留未来跨产品 adapter 边界。

## 运行流

Uvicorn 导入 `main.py` 后由 bootstrap 创建应用，显式装配 44 张 ORM 表、152 个 path/176 个 operation 和 9 个真实任务模块；业务请求进入对应 module，再经 platform adapter 访问 MySQL、Celery 和 Local/MinIO。

## 边界

旧横向 `api/models/schemas/services/workers` 已退出；公共 Celery 字符串仍保留历史 `app.workers.tasks_*` 作为消息协议名，不是可导入模块路径。
