# 运维领域测试

## 现有覆盖

`test_daily_archive.py` 验证 manifest/ZIP、签名冻结和双登记；`test_storage_reconciliation.py`、`test_data_admin_api.py` 验证 scan/finding、分页、预检 token、幂等和四类处置；`test_control_plane_api.py`、`test_infrastructure_api.py`、`test_health.py` 验证 worker、queue、message、task、broker 与能力状态；`test_operational_list_filters.py` 覆盖排序/筛选边界。

## 证据边界

输入是隔离 MySQL/对象 adapter 与角色 fixture，输出是维护动作有权限、有界且可追踪的证据；不会把 RabbitMQ、Beat、Windows Agent 等合同当成已部署。
