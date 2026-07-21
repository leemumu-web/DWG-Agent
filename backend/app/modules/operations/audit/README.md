# 审计写入边界

## 现有实现

`interface.py` 是身份、项目、文件、Job、工作流等领域唯一允许调用的审计写入口；它组装 actor、action、resource、request IP、User-Agent 与 before/after JSON，并写入 operations 拥有的既有 `audit_logs` 表。审计读取 API 由 operations 顶层 route/service 提供。

## 输入与输出

输入是数据库 session、actor、动作、资源标识、请求上下文和变化快照，输出是一条可查询的 AuditLog 事实；写入规则由本入口统一，避免各域字段漂移。

## 边界

审计不是业务事务成功的替代，也不保存密钥、Authorization、Cookie 或对象字节；跨域调用不得直接复制模型/组装逻辑。
