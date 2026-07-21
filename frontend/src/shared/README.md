# 前端共享基础

## 现有实现

`api/` 提供 Axios、刷新合并和错误格式化；`auth/` 保存当前会话、守卫与初始化；`components/` 提供错误边界和网络状态；`styles/` 保存 reset、应用壳和跨域视觉规则。

## 输入与输出

它接收 Vite API base URL、后端 access/refresh 合同和浏览器网络事件，输出所有 feature 可复用的传输、会话和 UI 原语。

## 边界

shared 不得导入任何 feature，也不拥有项目、文件、Job 或工作流 DTO/写操作。
