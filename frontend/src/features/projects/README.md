# 项目 (Projects)

## 现有实现

`project.ts` 定义 `Project` 类型；`projects.api.ts` 通过 `GET /workflows/projects` 获取项目列表（原 `/projects` CRUD 端点已整合至工作流子系统）；`drawing.ts`/`drawings.api.ts`/`DrawingsPage.tsx` 提供图纸管理页面；`index.ts` 为统一导出点。

## 边界

项目创建已迁移至 `POST /workflows/projects`；项目列表仅返回只读投影；成员管理不再提供 HTTP 端点。
