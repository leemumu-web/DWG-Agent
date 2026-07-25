# BH Uniform Metric Scale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在单一 BH 候选流程中，以长度、截面尺寸和物理孔径证据证明并恢复统一视图比例。

**Architecture:** 新建独立的候选尺度证明模块。求解器在候选枚举阶段解析一次尺度，非 1:1 且证明完整时生成等比例候选工作副本，之后继续调用原制造降低和约束系统；1:1 候选保持恒等路径。

**Tech Stack:** Python 3.11、ezdxf、pytest、现有 BH 事实 IR/假设求解器/证明系统。

## Global Constraints

- 不修改源 DXF、BOX 流程或 BOX 黄金样例。
- 不硬编码样本文件名或固定比例。
- 正常 1:1 图不新增孔标必需条件。
- 非 1:1 恢复必须同时满足长度、截面尺寸、孔径和等比一致性。
- 不提交 Git；当前工作区已有修改全部保留。

---

### Task 1: 候选尺度证明与统一实体变换

**Files:**
- Create: `src/steel_dxf_split/bh_metric_scale.py`
- Modify: `src/steel_dxf_split/bh_knowledge.py`
- Test: `tests/bh_v152/test_bh_uniform_metric_scale.py`

**Interfaces:**
- Produces: `resolve_view_metric_scale(main, flange, metadata, annotations, instances, policy) -> ViewMetricScaleResolution`
- Produces: `scale_part_block(block, factor) -> PartBlock`
- Produces: `scale_runtime_instances(instances, factor) -> list[BHBlockInstance]`

- [ ] **Step 1: Write failing unit tests**

测试 `0.5×/1.5×/2×` 一致证据得到相应恢复系数；孔径冲突、缺少孔径证据和 X/Y 非等比不得授权；`1×` 返回恒等模式。

- [ ] **Step 2: Run tests and confirm failure**

Run: `.venv\Scripts\python.exe -m pytest tests/bh_v152/test_bh_uniform_metric_scale.py -q`

Expected: FAIL，因为尺度模块尚不存在。

- [ ] **Step 3: Implement the scale model**

实现不可变证据记录与解析结果；使用相对离散度判断共识，非 1:1 必须有来源绑定的 Bolt/CIRCLE 与孔径标注。使用 `Matrix44.scale(factor, factor, 1)` 变换候选工作副本并保留来源 ID。

- [ ] **Step 4: Run unit tests**

Run: `.venv\Scripts\python.exe -m pytest tests/bh_v152/test_bh_uniform_metric_scale.py -q`

Expected: PASS。

### Task 2: 接入唯一假设求解流程

**Files:**
- Modify: `src/steel_dxf_split/bh_hypothesis.py`
- Modify: `src/steel_dxf_split/bh_solver.py`
- Modify: `src/steel_dxf_split/bh_constraints.py`
- Test: `tests/bh_v152/test_bh_uniform_metric_scale.py`

**Interfaces:**
- Consumes: `ViewMetricScaleResolution`
- Produces: `ViewPairHypothesis.metric_scale`

- [ ] **Step 1: Add failing integration assertions**

将代表性 BH 样本整体缩放后编译，要求制造板件尺寸与原样本一致；比例冲突样本必须拒绝。

- [ ] **Step 2: Integrate scale resolution into candidate enumeration**

每个有序视图对只解析一次比例。仅授权候选使用变换后的 `PartBlock`；运行时实体按比例缓存，候选只调用一次 `lower_bh_assembly`。

- [ ] **Step 3: Add critical scale proof**

在证明报告中记录恒等、已授权恢复或证据冲突；比例恢复不得绕过原投影、装配和制造证明。

- [ ] **Step 4: Run focused solver tests**

Run: `.venv\Scripts\python.exe -m pytest tests/bh_v152/test_bh_uniform_metric_scale.py tests/bh_v152/test_bh_solver_search.py tests/bh_v152/test_bh_negative_semantics.py -q`

Expected: PASS。

### Task 3: 真实三图与兼容性验证

**Files:**
- No source changes.

- [ ] **Step 1: Compile the three source DXFs in an isolated output directory**

输入为 `D:\DevData\final dxf\BH` 中的 `a1-3-cb-234/258/414.dxf`，要求三者不再因 `BH-PROOF-NO-VALID-HYPOTHESIS` 失败。

- [ ] **Step 2: Run representative regression tests**

Run: `.venv\Scripts\python.exe -m pytest tests/bh_v152/test_bh_semantic_solver.py tests/bh_v152/test_bh_representation_invariance.py tests/bh_v152/test_bh_compilation_contract.py -q`

Expected: PASS。

- [ ] **Step 3: Compare existing accepted corpus**

重跑现有自动通过 BH，比较制造几何/语义指纹；任何差异均阻止启用。

- [ ] **Step 4: Inspect working-tree scope**

Run: `git status --short`

Expected: 仅出现本功能文件以及任务开始前已有的用户修改。
