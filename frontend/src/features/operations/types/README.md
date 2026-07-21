# 运维类型合同

## 现有实现

`audit.ts` 定义审计记录、分页和筛选；`dataAdmin.ts` 定义文件登记、对象条目、transfer、scan run/finding、预检和 remediation 响应。control-plane/system 的紧邻 DTO 保留在其 API 文件，避免无关大类型单体。

## 输入、输出与边界

输入是后端 OpenAPI 对应 JSON，输出是 API、面板和表格共享的 TypeScript 合同。字段、枚举或 nullable 变化必须同步后端 schema、生成 API 文档和契约测试，不能用 `any` 掩盖漂移。
