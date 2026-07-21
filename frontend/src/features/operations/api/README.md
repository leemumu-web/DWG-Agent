# 运维 API

## 现有实现

`system.ts` 查询健康、基础设施和统计；`dataAdmin.ts` 查询 files、objects、transfers、scans 并执行扫描/处置；`controlPlane.ts` 查询 worker、queue、message、task 与通信状态；`auditLogs.ts` 查询审计记录。

## 输入、输出与边界

输入是分页/筛选、预检 token、幂等键和管理员动作，输出是类型化后端响应。本区不拼接 SQL/object key，不把未部署服务标为 online，也不绕过后端二次确认和权限。
