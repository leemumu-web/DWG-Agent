# 安全原语

## 现有实现

`tokens.py` 使用 pwdlib 推荐 PasswordHash 完成密码哈希/验证，并生成带 `sub`、唯一 `jti`、`iat`、`exp`、`type` 的 access/refresh JWT；`decode_token` 按配置算法和密钥验证签名/时效。

## 输入与输出

输入是明文密码、已存 hash、subject、claims 和密钥配置，输出是 hash、校验结果或已验证 token payload。

## 边界

token blacklist、密码更新时间、refresh cookie、角色与项目访问策略归 identity/projects；本区不负责会话持久化或授权决定。
