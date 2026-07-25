# React 源码总入口

## 现有实现

`main.tsx` 挂载 React；`App.tsx` 保留稳定根组件并委托 app provider/router；`vite-env.d.ts` 只引入 Vite 客户端环境类型；`app/` 组合 Provider、路由和应用壳；`shared/` 提供无业务归属的 HTTP、认证与通用 UI；`features/` 保存 12 个业务 owner。全局样式从 `shared/styles/index.css` 载入，各 feature 自行加载局部样式。

## 业务流与依赖

浏览器先由 shared auth 恢复会话，再由 app router 按权限挂载各业务页面；页面只能经 feature 的 `index.ts` 互相引用。输入是 Vite 环境和后端 `/api/v1` 合同，输出是生产管理 SPA。

## 边界

这里不保存后端业务真相；旧顶层 `api/components/hooks/stores/types/utils` 已退役，新增业务必须进入对应 feature。
