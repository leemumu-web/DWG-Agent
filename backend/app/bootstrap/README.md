# Application bootstrap

`bootstrap/` 是允许组合 platform 和 business modules 的唯一应用装配层。

| 文件 | 责任 |
|---|---|
| `application.py` | FastAPI lifespan、中间件、异常、health 和 ASGI app |
| `router.py` | 唯一允许直接装配领域 route routers 的位置；按历史顺序保持 HTTP 注册契约 |
| `model_registry.py` | Alembic、测试和应用共享的显式 ORM module registry |
| `task_registry.py` | Celery 共享的显式任务 module registry |
| `seed.py` | 组合身份模型、平台 Session 和密码原语的幂等初始数据命令 |

`app.main:app` 是稳定外部入口；业务模块和 platform 都不得反向导入 bootstrap。
