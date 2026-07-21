# 后端业务模块

## 现有业务 owner

`identity` 管用户/角色/token，`projects` 管项目/图纸，`files` 管文件登记与存储事务，`jobs` 管 attempt/结果/复核，`cad_processing` 与 `dxf_classification` 管三条 CAD 管线和分流，`excel_processing` 管 Excel Final，`workflows` 管生产批次，`operations` 管审计/归档/对账/控制面，`automation` 管已交付 Agent 账本和未实现执行合同。

## 调用与输出

每个 owner 同地保存 route、schema、model、service/task 及 `interface.py`，输入是已验证 HTTP/task/跨域请求，输出是自己拥有的数据库状态、公开 API 或任务结果。模块可以依赖 `app.platform`；跨模块只能调用目标 `interface.py`。

## 边界

platform 不得反向导入业务模块。旧横向 `api/models/schemas/services/workers` 已退出；`app.workers.tasks_*` 只保留为 Celery 消息协议名，不是兼容源码包。placeholder/external 状态必须继续如实暴露。
