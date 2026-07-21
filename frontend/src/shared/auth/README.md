# 共享认证会话

## 现有实现

`session.ts` 管理 sessionStorage access token；`store.ts` 保存用户/初始化状态；`api.ts` 调用 current-user、refresh、logout；`useAuthInit.ts` 在启动时恢复会话；`guards.tsx` 提供登录和角色路由门禁；`types.ts` 定义共享 session 形状。

## 业务流

应用启动先尝试 access token，再依赖 HttpOnly cookie 刷新并加载当前用户；退出同时请求服务端吊销并清理本地状态。输出供 router、菜单和 feature 权限提示使用。

## 边界

守卫只改善交互，FastAPI 必须再次授权；用户/角色管理归 identity feature。
