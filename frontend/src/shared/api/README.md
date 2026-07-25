# 共享 API 客户端

## 现有实现

`client.ts` 创建 Axios client，注入 sessionStorage access token，并把并发 401 合并为一次 `/auth/refresh`；刷新失败会清理会话。`error.ts` 解析 FastAPI detail、字段校验、错误码、request ID 与有界操作恢复建议；`index.ts` 是公共出口。

## 输入与输出

输入是相对 `/api/v1` 请求、HttpOnly refresh cookie 和标准错误 envelope；输出是带认证、可重试且可向操作员解释的请求结果。

## 边界

本区不声明任何业务 URL，也不把 GET 重试规则套到上传/删除等写操作。
