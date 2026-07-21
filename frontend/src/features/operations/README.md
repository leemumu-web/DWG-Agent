# 运维与数据控制台

## 现有实现

`pages/` 提供审计页和数据控制台；`api/` 调用 system、data-admin、control-plane、audit；`types/` 保存运维 DTO；`components/data-console/` 拆分六个面板；`DailyArchivePanel`、`RemediationDrawer` 承担受控维护动作；`styles.css` 保存局部布局。

## 业务流

管理员先查看 MySQL、MinIO、Worker 和通信事实，再对归档或一致性 finding 执行预检、确认和有界处置。输出包含 run、finding、transfer、archive 记录及 request ID。

## 安全与未完成边界

页面不得绕过签名 token、actor 绑定、幂等键、数量/字节上限或永久删除确认词；RabbitMQ、Beat、Outbox 与 Windows Agent 只能显示未部署合同。
