# 工作流测试

## 现有覆盖

`test_workflow_framework.py`、`test_workflow_production.py`、`test_workflow_api.py` 覆盖模板、创建、启动、确认、取消、stage/artifact 和权限；`test_production_project_api.py` 覆盖 Project 与唯一完整生产 Workflow 的原子创建、旧入口防绕过和失败回滚；`test_workflow_input_service.py`、`test_workflow_input_api.py` 覆盖 `.xls`/`.xlsx` 单文件与 DWG 文件夹的分步提交、格式/配对、服务器派生 DXF、补交、冻结和 drawing unit；`test_workflow_dxf_contracts.py` 锁定源 DWG 仅限输入阶段、下游 DXF 文件结构和各阶段必需产物；`test_workflow_batch_exports.py` 覆盖四类固定目录、原文件名、流式下载、下载失败保留、运行阶段门禁和确认后物理释放；`test_workflow_retention.py` 覆盖完整关系收集、共享引用门禁、逐对象校验、完整 ZIP、入队失败保留、删除中断补偿和安全重试；`test_workflow_retention_models.py` 锁定完整备份模型与迁移；`test_workflow_boundaries.py` 固定 owner/interface/route 顺序和留白能力。

## 证据边界

输入是 files/jobs/Stage fixture 与隔离数据库，输出是当前上传→转换→冻结→分类的后端证据；后续拆板、Windows/CAM 和最终屏障仍未实现。
