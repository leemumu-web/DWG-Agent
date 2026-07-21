# API 合同 E2E

## 现有场景

`api-contract.spec.ts` 覆盖健康、登录/刷新、文件上传/下载、Job/Result、工作流、系统和控制平面的关键方法、路径、鉴权与错误 envelope，并检查不会调用已退役 URL。

## 输入与证据边界

输入是可访问测试后端或受控响应，输出是浏览器实际请求合同证据；它不执行每个 CAD/Excel 核心算法，也不替代生成 OpenAPI 的后端合同测试。
新增/移动前端 API 时应在此证明浏览器使用的 method/path 与鉴权方式，字段级 schema 仍以生成 OpenAPI 和 TypeScript/source contract 为准。
