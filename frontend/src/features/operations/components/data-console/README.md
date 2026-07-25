# 数据控制台面板

## 现有实现

- `OverviewPanel.tsx`：文件、对象、流转、扫描汇总与最近扫描。
- `RuntimeCommunicationPanel.tsx`：API、Celery worker、queue、message、task 与 broker 声明。
- `MySqlWorkspace.tsx`：创建短时网关会话并嵌入 MySQL 结构/数据管理器。
- `FilesPanel.tsx`、`ObjectsPanel.tsx`、`TransfersPanel.tsx`：展示 MySQL 文件、MinIO 目录树和双系统流转；对象面板只向 admin 暴露上传、移动和软删除。
- `ConsistencyPanel.tsx`：发起扫描、查询 finding，并打开受控处置。
- `presentation.tsx`：状态、时间、容量和枚举文案，不发请求。

## 业务边界

输入来自 data-admin/control-plane 的真实响应，输出是登录用户可核对的数据库与存储事实。非 admin 保持只读；无扫描历史时只显示引导，不发送无效 findings 请求；MySQL broker 不能显示成 RabbitMQ 已部署。
