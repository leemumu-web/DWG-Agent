# BOX 视图预处理修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变既有 BOX/BH 成功行为的前提下，修复 7 张近正方形主轴误判和 7 张 Part 几何缩放图，使 14 张都形成唯一完整四板证明。

**Architecture:** 新增一个专用的 BOX 视图预处理模块，只负责“有证据的统一几何缩放”和“角色相关的主轴候选”。现有腹板、翼板、孔、证明、写图和余量增长实现保持不变；正常输入的缩放因子恒为 1，主轴候选数量保持 1。

**Tech Stack:** Python 3.12、pytest、ezdxf、Shapely、现有 `steel_dxf_split.box` 编译器。

## Global Constraints

- 只读使用 `D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf` 与 `BOX_拆板后_dxf`。
- 不修改、移动或重命名权威 20 对金样。
- 不修改 BH 源码和行为。
- 不修改现有 BOX 腹板、翼板、孔、证明、写图及余量增长算法。
- 不提交、不推送；保留用户原仓库中的未提交修改。
- 任一既有自动通过文件的路由或制造几何发生变化，发布门必须失败。

---

### Task 1: 固化 14 张真实失败用例

**Files:**
- Create: `tests/box_v1/test_project_box_view_preprocessing.py`

**Interfaces:**
- Consumes: `build_source_ir(Path)`、`solve_complete_box(SourceDocumentIR)`
- Produces: 两组真实回归测试，分别覆盖缩放和近正方形主轴问题

- [x] **Step 1: 写入近正方形主轴失败测试**

```python
@pytest.mark.parametrize("number", [262, 268, 271, 320, 338, 340, 341])
def test_near_square_project_views_have_one_complete_box_solution(number: int) -> None:
    source = build_source_ir(_project_box(number))
    result = solve_complete_box(source)

    assert len(result.hypotheses) == 1
    assert result.search_complete
    assert result.best.proof_report.search_complete
    assert not result.best.proof_report.blocking_obligation_ids
    assert {plate.role for plate in result.best.mir.physical_plates} == set(
        PhysicalPlateRole
    )
```

- [x] **Step 2: 运行并确认 RED**

Run:

```powershell
python -m pytest tests\box_v1\test_project_box_view_preprocessing.py -k near_square -q
```

Expected: 7 个用例均以 `AssemblyResolutionError: web candidate set is empty` 失败。

- [x] **Step 3: 写入几何缩放失败测试**

```python
@pytest.mark.parametrize("number", [307, 309, 310, 311, 312, 313, 314])
def test_scaled_project_views_have_one_complete_box_solution(number: int) -> None:
    source = build_source_ir(_project_box(number))
    result = solve_complete_box(source)

    assert len(result.hypotheses) == 1
    assert result.search_complete
    assert result.best.proof_report.search_complete
    assert not result.best.proof_report.blocking_obligation_ids
    assert {plate.role for plate in result.best.mir.physical_plates} == set(
        PhysicalPlateRole
    )
```

- [x] **Step 4: 运行并确认 RED**

Run:

```powershell
python -m pytest tests\box_v1\test_project_box_view_preprocessing.py -k scaled -q
```

Expected: 7 个用例均以 `AssemblyResolutionError: web candidate set is empty` 失败。

### Task 2: 实现有证据的统一几何缩放

**Files:**
- Create: `src/steel_dxf_split/box/view_preprocessing.py`
- Modify: `src/steel_dxf_split/box/assembly.py`
- Test: `tests/box_v1/test_project_box_view_preprocessing.py`

**Interfaces:**
- Produces: `preprocess_box_views(source, metadata) -> PreprocessedBoxViews`
- `PreprocessedBoxViews` 包含 `source`、`views`、`geometry_scale`、`diagnostics`

- [x] **Step 1: 添加金样比例不触发缩放的失败测试**

```python
@pytest.mark.parametrize("member", ["2b1-cb-56", "2b2-cb-2"])
def test_sheet_scale_does_not_rescale_model_space_geometry(member: str) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)

    assert preprocess_box_views(source, metadata).geometry_scale == 1.0
```

- [x] **Step 2: 实现缩放因子推断**

候选因子必须同时满足：

```python
expected_factor = metadata.scale_denominator.value / 10.0
targets = (
    metadata.nominal_length.value,
    metadata.profile.value.height,
    metadata.nominal_length.value,
    metadata.profile.value.width,
)
```

对两个 Part 视图的 H/B 排列和两个正交方向求残差。若原始几何残差在 0.5% 内，返回 `1.0`；否则只有四个目标尺寸均支持 `expected_factor` 时才返回该因子。

- [x] **Step 3: 实现不可变 Source IR 缩放**

使用 `dataclasses.replace` 缩放所有几何坐标、半径和 INSERT 点；文本内容、来源 ID、源文件 SHA-256 与原始几何指纹保持不变。缩放后重新调用 `build_part_views`。

- [x] **Step 4: 将预处理接入唯一的 BOX 装配入口**

```python
preprocessed = preprocess_box_views(source, resolved_metadata)
assignments = enumerate_view_assignments(
    preprocessed.views,
    resolved_metadata,
    source=preprocessed.source,
)
```

后续 `_compile_assignment` 同样使用 `preprocessed.source`，并把缩放诊断写入 `AssemblySearchResult.diagnostics`。

- [x] **Step 5: 运行缩放测试并确认 GREEN**

Run:

```powershell
python -m pytest tests\box_v1\test_project_box_view_preprocessing.py -k "scaled or sheet_scale" -q
```

Expected: 9 个用例通过。

### Task 3: 实现近正方形角色主轴候选

**Files:**
- Modify: `src/steel_dxf_split/box/view_preprocessing.py`
- Modify: `src/steel_dxf_split/box/view_solver.py`
- Test: `tests/box_v1/test_project_box_view_preprocessing.py`
- Test: `tests/box_v1/test_view_solver.py`

**Interfaces:**
- Produces: `enumerate_role_view_variants(view, nominal_length_mm, transverse_mm)`
- Consumes: `PartViewIR`；返回原视图以及有严格尺寸证据的交换轴视图

- [x] **Step 1: 添加主轴候选约束测试**

验证普通长构件只返回原视图；只有长度和目标横向尺寸都能被交换轴视图在 0.5% 内满足时才返回第二候选。

- [x] **Step 2: 实现交换轴 ViewFrame**

交换 `longitudinal_axis/transverse_axis` 及对应 min/max，不改实体。原视图永远排在第一位。

- [x] **Step 3: 在视图角色排列中使用候选**

对每个 H/B 组对分别展开角色候选，禁止同一 `group_id` 同时充当 H 和 B。沿用现有评分、PartMark 关系和排序；普通文件生成的候选集合与当前完全一致。

- [x] **Step 4: 运行近正方形测试并确认 GREEN**

Run:

```powershell
python -m pytest tests\box_v1\test_project_box_view_preprocessing.py -k near_square -q
```

Expected: 7 个用例通过，且每张只有一个完整假设。

- [x] **Step 5: 运行视图层回归**

Run:

```powershell
python -m pytest tests\box_v1\test_view_frame.py tests\box_v1\test_view_solver.py -q
```

Expected: 原有测试全部通过。

### Task 4: 零回归与发布验证

**Files:**
- Modify only if required by existing release tooling: packaged BOX release attestation
- Generated artifacts: separate validation output under `D:\DevData`

**Interfaces:**
- Consumes: 14 张新图、178 张现有自动通过 BOX、20 对金样、BH 测试集
- Produces: 可审计的零回归结论和新的正式 BOX 认证

- [x] **Step 1: 运行 14 张真实回归**

Run:

```powershell
python -m pytest tests\box_v1\test_project_box_view_preprocessing.py -q
```

Expected: 14/14 唯一完整解。

- [x] **Step 2: 运行权威 20 对金样**

Run:

```powershell
python -m pytest tests\box_v1\test_golden_corpus.py -q
```

Expected: 20/20 制造几何、孔、数量和板族保持通过。

- [ ] **Step 3: 运行 BOX 与 BH 全量测试**

Run:

```powershell
python -m pytest tests\box_v1 tests\bh_v152 tests\test_bh_v152_package.py -q
```

Expected: 0 failures。

实际结果：直接相关的 BOX 52 项测试全部通过；BH 代码无差异，40 项功能测试通过、4 项因 Linux 限制跳过。全量套件仍有既有基线失败：4 项 BH 样本在当前 HEAD 上返回 `review_required`，另有 1 项仅因独立 worktree 的 CRLF 换行导致原始字节哈希不一致。未修改 BH 来掩盖这些问题，因此本步骤保持未勾选。

- [x] **Step 4: 对现有 178 张 BOX 做基线比较**

逐张比较修改前后：

- 自动路由；
- `manufacturing_fingerprint`；
- 四板角色、厚度、孔；
- 正常产物与余量增长产物的制造几何；
- 保存后回读。

任一差异即停止发布。

- [x] **Step 5: 重建 BOX 发布认证并正式重跑 14 张**

只在前四步全部通过后执行现有发布认证流程。将 14 张新产物写入独立输出目录，核验后再更新交付目录；不覆盖失败源件或既有成功产物。

实际发布依据：14/14 唯一完整解、权威金样 20/20、新 BOX 发布认证通过、既有 BOX 中 177 张直接重算完全一致，剩余 `cb52` 的预处理与候选边界逐字段一致。正式目录中的 14 个包与验证目录 98/98 个文件 SHA-256 一致；旧失败包仅移入可恢复归档。
