# 基础设施合同测试

## 现有覆盖

`test_compose.py`、`test_nginx_contract.py`、`test_config*.py`、`test_migrations.py`、`test_db_session.py` 锁定 13 个服务、端口、volume、环境键、MySQL pool 和 Alembic；`test_celery_recovery.py`、`test_celery_minio_deployment.py` 覆盖 SQL transport、MinIO 和 worker 信号；`test_scripts.py`、`test_storage_operations.py`、`test_forward_to_win11_script.py` 执行 Shell、facade、备份/处置和转发合同。

## 证据边界

输入是仓库配置、临时环境与可选活动服务，输出是部署/运维入口可解析且声明真实的证据；被跳过的活动探针不能算生产验收。
