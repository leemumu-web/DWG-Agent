# Identity HTTP 路由

## 现有实现

`sessions.py` 提供登录、refresh、logout、me；`users.py` 提供管理员用户 CRUD/状态/角色；`roles.py` 提供角色、permission 和保护规则；`router.py` 保持原有路径/operationId/顺序。

## 输入、输出与边界

输入是凭据、refresh cookie、JWT 与管理员命令，输出是 token/cookie、用户/角色 DTO 和审计写入。每个管理端点执行服务端授权，浏览器按钮隐藏不是安全边界。
