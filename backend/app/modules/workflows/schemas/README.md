# Workflow Schema

## 现有实现

`orchestration.py` 定义模板、run/stage/artifact、命令和能力状态 DTO；`production_projects.py` 定义项目与唯一完整工作流的原子创建 DTO；`intake.py` 定义输入登记、配对、转换、冻结和 drawing unit DTO；`__init__.py` 聚合公开 schema。

## 输入、输出与边界

输入是不可信 HTTP JSON，输出是稳定阶段名和严格“多个 DWG + 一个 Excel”合同。状态机、文件登记和 Stage 调度留在 service/intake/execution。
