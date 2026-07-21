# Workflow 持久化模型

## 现有实现

`orchestration.py` 定义 WorkflowRun、WorkflowStageRun、WorkflowArtifact；`intake.py` 定义 WorkflowInput 与 WorkflowDrawingUnit；`__init__.py` 聚合五张表并进入 model registry。

## 输入、输出与边界

输入是 project/file/job 关联、阶段状态和冻结元数据，输出是工作流、阶段、产物、输入和逐图单元事实。模型不执行 Stage，也不使 placeholder 阶段自动可用。
