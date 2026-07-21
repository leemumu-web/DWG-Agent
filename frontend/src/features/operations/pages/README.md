# 运维页面编排

## 现有实现

`AuditLogsPage.tsx` 管理 actor、action、resource、time 筛选、分页和详情；`InfrastructurePage.tsx` 只组合 overview、runtime、files、objects、transfers、consistency 六个面板及 URL tab。

## 输入、输出与边界

输入是路由查询参数、当前权限与子面板，输出是稳定 `/audit-logs` 和 `/infrastructure` 页面。页面层不复制 API 查询、扫描处置或状态格式化逻辑；管理员/审计员限制仍由后端最终执行。
