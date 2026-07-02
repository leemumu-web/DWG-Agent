# CAD Worker 协议占位

当前阶段不实现 Windows ZWCAD Worker，只保留目录与协议说明。

后续建议接口：

```text
GET    /api/v1/internal/cad-worker/jobs/next
PATCH  /api/v1/internal/cad-worker/jobs/{job_id}
POST   /api/v1/internal/cad-worker/heartbeats
```

安全边界：

- 内网访问。
- API Key 或 mTLS。
- 每个任务独立 sandbox。
- 回传 JSON 必须做 schema 校验。
