# 架构测试

## 现有覆盖

`test_contract_snapshot.py`、`test_module_catalog.py` 固定 133 path/156 operation、42 表、13 task、12 task route、Compose/Alembic/React 路由；`test_platform_boundaries.py` 锁定 platform/bootstrap 依赖方向与 registry；`test_identity_projects_boundaries.py`、`test_files_boundaries.py`、`test_jobs_boundaries.py`、`test_cad_processing_boundaries.py`、`test_excel_processing_boundaries.py`、`test_workflow_boundaries.py`、`test_operations_automation_boundaries.py` 分别检查领域公开 interface、表 owner、route/task 合同、退役路径和真实 placeholder；`test_patch_targets.py` 验证字符串 mock target；`test_test_suite_layout.py` 与 `test_partition_docs.py` 约束测试/README 分区和每个直接源码的就地说明。

## 证据边界

输入是可导入应用、AST 和架构 JSON，输出是重构没有静默改变 owner/外部合同的静态证据；它不证明 MySQL、MinIO、ODA 或浏览器运行成功。
