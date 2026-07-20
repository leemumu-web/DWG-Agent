# 备份与恢复

Status: manual tooling exists; scheduled off-site backup is not implemented.

当前 `scripts/docker.sh backup/restore` 可生成 MySQL dump、归档 MinIO volume 并校验摘要，但数据库与对象快照不是单一原子事务。每日一键归档是业务整理产物，不是数据库/对象灾难恢复备份。

目标完成证据包括：维护窗口或一致快照策略、离机加密副本、保留/清理、secret 备份、独立主机恢复演练、RPO/RTO 测量和失败告警。恢复是破坏性操作，只能按[运维指南](../../../docs/guides/operations.md)在明确目标环境执行。
