# 身份测试

## 现有覆盖

`test_token_lifecycle.py` 验证 access/refresh、cookie、轮换、blacklist、密码更新时间、退出和并发刷新；`test_rbac_deep.py` 验证用户/角色/permission 管理、内置角色保护、跨项目/全局权限和拒绝路径。

## 证据边界

输入是隔离数据库、JWT 配置和认证 fixture，输出是身份会话/RBAC 不回退的证据；浏览器存储行为另由前端合同与 Playwright 验证。
