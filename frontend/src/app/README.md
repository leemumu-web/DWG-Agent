# 前端应用装配

## 现有实现

- `providers.tsx`：配置 React Query、Ant Design 中文 locale 和全局错误边界。
- `router.tsx`：声明登录、项目、生产流程、四条处理管线、文件、Job、复核、用户/角色、运维和个人资料 URL，并应用登录/角色门禁。
- `layout.tsx`：桌面/移动导航、用户菜单和页面 Outlet。

## 业务流与边界

它需要 shared 会话以及各 feature `index.ts`，输出是供 `main.tsx` 挂载的完整应用。这里只组合页面，不定义领域 API、DTO 或写操作；服务端仍是最终授权边界。
