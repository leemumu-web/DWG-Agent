# Identity 持久化模型

## 现有实现

`user.py` 定义用户状态、密码更新时间和角色关系；`role.py` 定义 role、permission 及关联表；`token_blacklist.py` 保存 refresh/access `jti` 吊销/到期事实；`__init__.py` 聚合模型注册。

## 输入、输出与边界

输入是平台 SQLAlchemy Base 和身份状态，输出是用户、全局 RBAC 与 token 吊销的数据库事实。项目成员关系归 projects，密码/JWT 原语归 platform security。
