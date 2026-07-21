# 运维基础设施

## 现有内容

`backup/` 描述 MySQL、MinIO/Local 分支、恢复顺序与备份窗；实际手动入口为 `scripts/docker.sh backup|restore`。`monitoring/` 保存指标/日志/告警目标说明，当前没有完整 Prometheus/Grafana/告警 service 部署。

## 输入、输出与未完成边界

输入需要生产凭据、外部调度、保留策略、异地副本和告警接收端，输出才是可运行运维体系。仓库脚本通过不等于定时备份、恢复演练或监控已上线。
