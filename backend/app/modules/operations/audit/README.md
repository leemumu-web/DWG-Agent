# Audit interface

`interface.py` 是跨领域审计写入口，当前实现写入既有 `audit_logs` ORM 表。身份、项目、文件、Job 和工作流只能依赖该入口，不应直接复制 actor、request IP、User-Agent 或 before/after JSON 的组装规则。

审计读取路由、ORM 与完整 operations 目录将在 operations 垂直切片中迁入；此处已经是稳定目标路径，不是空占位。
