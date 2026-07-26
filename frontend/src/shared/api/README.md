# 共享 API 客户端

## 现有实现

`client.ts` 创建 Axios client，注入 sessionStorage access token，并把并发 401 合并为一次 `/auth/refresh`；刷新失败会清理会话。`error.ts` 在内部保留状态、错误码和请求编号用于恢复判断，对外只输出有界中文原因、请求编号和可执行建议；英文异常、堆栈、服务器路径、SQL 与框架诊断不会进入工人页面。`transfer.ts` 统一上传/下载字节进度、响应文件名、Blob 保存和下载错误解析；`index.ts` 是公共出口。

## 输入与输出

输入是相对 `/api/v1` 请求、HttpOnly refresh cookie 和标准错误 envelope；输出是带认证、可重试且可向操作员解释的中文请求结果。请求编号用于管理员查后台记录，不把后台记录本身展示在浏览器。

## 边界

本区不声明任何业务 URL，也不把 GET 重试规则套到上传/删除等写操作。
