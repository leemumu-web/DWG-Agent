# 项目与图纸目录

## 现有实现

`ProjectsPage.tsx` 管理项目、成员和进入入口；`DrawingsPage.tsx` 展示图纸与版本；`projects.api.ts`、`drawings.api.ts` 调用稳定端点；`project.ts`、`drawing.ts` 定义 DTO，`index.ts` 暴露公共能力。

## 业务流

输入是当前身份、项目成员权限和后端项目/图纸事实，输出是生产批次可引用的 project/drawing 标识与导航。

## 边界

项目目录不拥有文件字节、CAD Job 或 workflow 状态；这些能力分别经 files、jobs、workflows 公共入口使用。
