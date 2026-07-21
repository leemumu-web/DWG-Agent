# 前端业务分区

## 现有实现

11 个 owner 为 automation、cad-processing、dashboard、excel-processing、files、identity、jobs、operations、projects、reviews、workflows。每个目录同时拥有该业务需要的页面、API、类型、hook/组件和局部样式，并通过 `index.ts` 公开允许复用的符号。

## 业务规则

feature 可以依赖 `shared`；跨 feature 只能导入目标 `index.ts`，避免重新形成横向 API/type 目录。输入是后端稳定合同和共享会话，输出是可由 app router 装配的业务切片。

## 能力边界

目录存在不代表核心执行已交付；automation 和 workflow 后续阶段必须按后端 capability/status 如实显示。
