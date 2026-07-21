# Identity Schema

## 现有实现

`auth.py` 定义 login、token、refresh/current-user 合同与凭据约束；`user.py` 定义用户、角色、permission 的创建/更新/列表/响应 DTO；`__init__.py` 聚合公共 schema。

## 输入、输出与边界

输入是不可信 HTTP JSON，输出是经 Pydantic 验证的稳定合同。ORM、密码哈希、cookie 和授权决策分别留在 models、platform security、routes/service。
