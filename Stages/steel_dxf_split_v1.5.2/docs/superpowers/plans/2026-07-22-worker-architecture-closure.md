# BH/BOX 拆板 Worker 架构收口实施计划

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**目标：** 在不重写 BH v1.5.2 与 Project2 BOX 核心算法的前提下，将当前“主链路已连通”的程序收口为只有一个公开入口、一个结果契约和一个交付事务的拆板 Worker。

**架构：** 顶层 `steel_dxf_split.pipeline` 私有识别构件类型并调用唯一的 BH 或 BOX 核心；两个领域保留各自制造语义和验证规则，顶层只统一结果与交付。删除 BOX 旧 CLI/pipeline 编排，不增加 `application/`、adapter、backend 或 fallback 层。

**技术栈：** Python 3.11+、pytest、ezdxf、Shapely、标准库文件事务、wheel 安装态验收。

---

## 任务 A：唯一公开入口

**文件：**

- 修改：`tests/test_box_architecture.py`
- 修改：`tests/box_v1/test_ground_truth_firewall.py`
- 修改：`tests/box_v1/test_pipeline.py`
- 修改：`tests/box_v1/test_source_import_verifier.py`
- 修改：`scripts/verify_box_v1_source.py`
- 修改：`src/steel_dxf_split/box/__init__.py`
- 删除：`src/steel_dxf_split/box/cli.py`
- 删除：`src/steel_dxf_split/box/batch_cli.py`
- 删除：`src/steel_dxf_split/box/pipeline.py`

- [x] 先写架构测试，要求 BOX 包不再提供 `split_dxf`，三个旧编排文件不存在。
- [x] 运行测试并确认它因现有旧入口而失败。
- [x] 将仍有价值的旧 pipeline 端到端测试迁到统一入口或 BOX compiler。
- [x] 删除旧编排文件，并让 `box.__init__` 只保留版本元数据。
- [x] 更新 Project2 v1.0.0 来源校验：分别登记保留、热修、集成适配和明确退役文件。
- [x] 运行 BOX 架构、来源和核心路由测试，确认通过。

## 任务 B：统一 Worker 结果契约

**文件：**

- 新增：`tests/test_worker_result_contract.py`
- 修改：`src/steel_dxf_split/pipeline.py`
- 修改：`src/steel_dxf_split/cli.py`
- 修改：`src/steel_dxf_split/batch_cli.py`
- 修改：受统一处置值影响的定向测试

- [x] 先写 BH/BOX 两条假编译路径测试，要求返回同一组顶层字段和同一摘要键集合。
- [x] 运行测试并确认现有 `SplitResult` 缺少统一字段、CLI 仍按领域解析而失败。
- [x] 在顶层 pipeline 内归一化 `automation_route`、`disposition`、证明、诊断、预览和计时元数据。
- [x] 由 `SplitResult.to_summary()` 生成统一 JSON；CLI 不再解析 BH/BOX 原生报告。
- [x] 批处理只消费统一处置值，但仍按领域调用各自原生预览验证器。
- [x] 运行单文件、批处理、BH 与 BOX 定向回归测试。

## 任务 C：单图交付事务

**文件：**

- 新增：`tests/test_worker_batch_transaction.py`
- 修改：`src/steel_dxf_split/batch_cli.py`

- [x] 先写故障注入测试：第二个文件提升失败时，不能留下新旧混合产物。
- [x] 运行测试并确认当前逐文件 `replace` 会留下半完成结果。
- [x] 为单张图的完整产物集合建立备份、提升、回滚和目录同步流程。
- [x] 保持“已完成单图不因后续图失败而回滚”的任务级语义，不实现整批回滚。
- [x] 运行事务、批处理和路由测试。

## 任务 D：验收与提交

**文件：**

- 修改：`CONTEXT.md`
- 修改：`README.md`（仅在现有说明与最终入口不一致时）
- 新增：`docs/superpowers/reports/2026-07-22-worker-architecture-closure-validation.md`

- [x] 运行 `git diff --check` 和架构定向测试。
- [x] 运行 Windows 全量 pytest 与 BH/BOX 代表性语料。
- [x] 构建 wheel，在隔离安装目录中验证只暴露统一入口且发布证明可用。
- [x] 在 Linux Python 3.12/glibc 容器完成无依赖结构安装态验收，并记录完整依赖发布门缺口。
- [ ] 经用户授权后，在具备完整依赖和字体的 Linux Worker 环境重跑 BH/BOX 算法发布门并生成新 BOX attestation。
- [x] 记录测试计数、失败/跳过项、产物哈希和剩余风险。
- [x] 提交架构收口；不 push、不合并主干、不打标签。
