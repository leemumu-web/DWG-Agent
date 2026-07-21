# 架构测试

## 现有覆盖

`test_contract_snapshot.py`、`test_module_catalog.py` 固定 114 path/135 operation、36 表、11 task、Compose/Alembic/React 路由；各 `test_*_boundaries.py` 检查 platform 与领域依赖、公开 interface、退役路径、router/task registry；`test_patch_targets.py` 验证字符串 mock target；`test_test_suite_layout.py` 与 `test_partition_docs.py` 约束测试/README 分区。

## 证据边界

输入是可导入应用、AST 和架构 JSON，输出是重构没有静默改变 owner/外部合同的静态证据；它不证明 MySQL、MinIO、ODA 或浏览器运行成功。
