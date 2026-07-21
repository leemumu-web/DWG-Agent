# 身份与权限

## 现有实现

`LoginPage.tsx` 登录并回到原目标；`ProfilePage.tsx` 展示当前账户；`UsersPage.tsx` 与 `users.api.ts` 管理用户状态/角色；`RolesPage.tsx` 与 `roles.api.ts` 管理角色权限；`styles.css` 保存登录和管理布局。

`index.ts` 只重导出 identity 页面、管理请求和 DTO，供 Router 与其他 feature 经稳定 facade 使用；它不重导出 shared auth 的 token/store 实现。

## 业务流

输入是 shared auth 会话和 identity 管理端点，输出是登录反馈、账户信息及管理员 RBAC 操作。所有失败显示后端原因与 request ID。

## 边界

access/refresh 生命周期和路由守卫归 shared auth；项目成员权限归 projects；UI 隐藏不替代服务端授权。
