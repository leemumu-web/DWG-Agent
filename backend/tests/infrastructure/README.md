# 基础设施合同测试

## 现有覆盖

`test_compose.py`、`test_nginx_contract.py`、`test_migrations.py`、`test_workflow_stage_migration.py`、`test_workflow_dxf_migration.py`、`test_db_session.py`、`test_business_time.py`、`test_beijing_time_migration.py`、`test_database_startup.py`、`test_database_wait.py` 锁定 16 个 Compose 服务、端口、volume、MySQL pool、北京时间墙上值边界、历史 `DATETIME` 转换、数据库等待门、启动迁移顺序、Alembic 与十阶段工作流迁移，并验证旧工作流向 DXF 规范流升级时遇到矛盾数据会失败关闭；`test_dispatcher_runtime.py` 锁定稳定 Celery task ID、批量消息和单用途 dispatcher 入口；`test_config.py` 验证 pydantic settings、MySQL/Celery URL、feature flag 与手册库凭据，`test_config_drift.py` 拒绝端口/网络/transport 漂移，`test_mysql_runtime.py` 拒绝 Redis 依赖及 SQL transport 不支持的 Flower/inspect 健康声明。`test_celery_recovery.py`、`test_celery_minio_deployment.py` 覆盖 SQL transport、MinIO 和 worker 信号；`test_scripts.py`、`test_storage_operations.py`、`test_forward_to_win11_script.py` 执行 Shell、facade、备份/处置和转发合同；`test_production_load_tools.py` 锁定真实生产 HTTP 压力驱动、数量守恒、下载包安全、资源采样和报告脱敏；`test_server_release.py` 锁定加密离线发布、服务器 Compose 渲染、独立安装器和镜像源码检查；`test_handbook_source_sync.py` 锁定唯一可信 `五金手册.xls` 的哈希、逐行来源、语义表计数、重复冲突保留及确定性数据库生成。

## 证据边界

输入是仓库配置、临时环境与可选活动服务，输出是部署/运维入口可解析且声明真实的证据；被跳过的活动探针不能算生产验收。
