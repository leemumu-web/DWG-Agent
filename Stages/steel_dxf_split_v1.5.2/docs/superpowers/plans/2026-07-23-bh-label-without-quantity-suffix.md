# BH 与 BOX 标签精简 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 BH 与 BOX 可见标签统一为精简的物理角色名称，同时保持数量语义、制造几何和余量处理不变。

**Architecture:** 调整 `canonical_bh_label()` 的可见文本契约并同步 `validate_bh_assembly()` 的精确标签检查，保留 `quantity` 参数和所有调用接口。BOX 的 `canonical_box_label()` 已符合全局精简契约，因此用全角色回归测试锁定其现有行为，不修改 BOX 几何算法。完成测试后重新运行 BH 数据并仅同步 BH 交付文件。

**Tech Stack:** Python 3.12、pytest、ezdxf、PowerShell。

## Global Constraints

- BH 与 BOX 均使用精简的物理角色名称。
- 保留内部 `quantity=2` 数量语义。
- 保留相同上下翼缘板合并为一个制造轮廓的现有行为。
- 不修改板件几何、孔洞、焊接余量、排版或报告数量字段。
- 两块不同翼缘板按既有顺序输出 `上翼`、`下翼`，不再显示 `-1`、`-2`。
- BOX 等价板输出 `腹`、`翼`，分开板输出 `上腹`、`下腹`、`上翼`、`下翼`。
- 完成验证后提交当前标签改动并本地合并到 `main`；不推送远程。

---

### Task 1: 锁定精简后的标签契约

**Files:**
- Modify: `tests/bh_v152/test_bh_engineering_regressions.py:27-31`
- Modify: `tests/bh_v152/test_bh_codegen_authority.py:77-126`
- Modify: `tests/box_v1/test_writer.py`
- Modify: `src/steel_dxf_split/bh_text.py:36-47`
- Modify: `src/steel_dxf_split/bh_validator.py:272-316`
- Modify: `tools/verify_bh_v152_source.py:50-103`
- Modify: `tests/test_bh_v152_package.py:41-66`

**Interfaces:**
- Consumes: `canonical_bh_label(part_number: str, role: str, index: int | None = None, quantity: int = 1) -> str`
- Produces: `web` 返回 `p=<编号>腹`，`flange` 返回 `p=<编号>翼`，不再追加 `×2`。
- Verifies: BOX 合并板返回 `腹`、`翼`，分开板返回 `上腹`、`下腹`、`上翼`、`下翼`。

- [ ] **Step 1: Write the failing test**

```python
def test_canonical_labels_use_compact_physical_roles_without_quantity_suffix() -> None:
    assert canonical_bh_label("member", "web") == "p=member腹"
    assert canonical_bh_label("member", "flange", quantity=2) == "p=member翼"
    assert canonical_bh_label("member", "flange", index=1) == "p=member上翼"
    assert canonical_bh_label("member", "flange", index=2) == "p=member下翼"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\bh_v152\test_bh_engineering_regressions.py::test_canonical_labels_use_compact_physical_roles_without_quantity_suffix -q
```

Expected: FAIL，实际值仍为 `p=member腹板`、`p=member翼缘板`。

- [ ] **Step 3: Write minimal implementation**

将 `canonical_bh_label()` 改为只根据角色和可选索引构造标签，不再追加数量：

```python
def canonical_bh_label(
    part_number: str,
    role: str,
    index: int | None = None,
    quantity: int = 1,
) -> str:
    if role == "web":
        base = f"p={part_number}腹"
    elif role == "flange":
        flange_role = {1: "上翼", 2: "下翼"}.get(index, "翼")
        base = f"p={part_number}{flange_role}"
    else:
        raise ValueError(f"Unsupported BH role: {role}")
    if index is not None and (role != "flange" or index not in {1, 2}):
        base += f"-{index}"
    return base
```

同步 `validate_bh_assembly()` 的 `canonical_labels` 检查，使单个翼缘轮廓期望 `p=<编号>翼`，两个不同翼缘轮廓期望 `p=<编号>上翼`、`p=<编号>下翼`，腹板期望 `p=<编号>腹`。

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\bh_v152\test_bh_engineering_regressions.py::test_canonical_labels_use_compact_physical_roles_without_quantity_suffix -q
```

Expected: PASS。

- [ ] **Step 5: Check the focused diff**

Run:

```powershell
git diff -- src\steel_dxf_split\bh_text.py tests\bh_v152\test_bh_engineering_regressions.py
```

Expected: 仅包含测试契约、标签文字、标签合法性检查和对应源码完整性哈希的最小修改。

### Task 2: 验证 BH、BOX 标签和全项目回归

**Files:**
- Test: `tests/bh_v152/`
- Test: `tests/box_v1/test_writer.py`
- Test: `tests/`

**Interfaces:**
- Consumes: Task 1 的新标签契约。
- Produces: BH 与统一程序的回归证据。

- [ ] **Step 1: Run the BH suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\bh_v152 -q
```

Expected: 0 failures。

- [ ] **Step 2: Run unified label and pipeline tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_unified_cli_contract.py tests\test_unified_paired_pipeline.py tests\test_paired_output_validation.py -q
```

Expected: 0 failures。

- [ ] **Step 3: Run the BOX label tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\box_v1\test_writer.py -q
```

Expected: 0 failures，合并与分开板件全部使用精简标签。

- [ ] **Step 4: Run the full suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: 0 failures。

### Task 3: 重新生成并验证 11 组 BH

**Files:**
- Replace generated files under: `output/auto_accepted/bh/`
- Preserve: `output/auto_accepted/box/`

**Interfaces:**
- Consumes: `D:\DevData\BYSJ零件图_分类\BH` 与 Task 1 的新标签实现。
- Produces: 11 组更新后的 BH 普通版、余量版、报告和预览。

- [ ] **Step 1: Check for open or locked BH output documents**

使用 ZWCAD COM 只读检查已打开文档。若待替换 BH 文件存在未保存改动，停止并请求用户授权，不强制关闭。

- [ ] **Step 2: Re-run the BH batch through the public CLI**

Run:

```powershell
.\.venv\Scripts\steel-dxf-split.exe `
  "D:\DevData\BYSJ零件图_分类\BH" `
  --output-dir ".\output" `
  --authorize-tekla-bh-single-part-profile project_tekla_bh_dxf_v1
```

Expected: exit code 0，11 项全部为 `auto_accepted`。

- [ ] **Step 3: Verify generated labels and paired outputs**

使用 ezdxf 读取 22 个 BH DXF，断言：

```python
assert all(decoded_label.endswith(("翼", "腹", "上翼", "下翼")) for decoded_label in all_labels)
assert all("×2" not in decoded_label for decoded_label in all_labels)
assert normal_and_allowance_count == 22
assert every_paired_validation_ok
```

同时检查现有 BOX DXF 标签符合精简契约，并断言 BOX 文件 SHA-256 未变化。

### Task 4: 同步桌面 BH 交付文件

**Files:**
- Replace: `C:\Users\lsp19\Desktop\BH_BOX拆板产出_2026-07-23_163212\BH\<编号>\<编号>_正常产出.dxf`
- Replace: `C:\Users\lsp19\Desktop\BH_BOX拆板产出_2026-07-23_163212\BH\<编号>\<编号>_余量产出.dxf`
- Preserve: desktop BH source files and entire BOX subtree.

**Interfaces:**
- Consumes: Task 3 验证通过的 BH DXF。
- Produces: 更新后的桌面交付目录。

- [ ] **Step 1: Copy only BH normal and allowance outputs**

按 11 个编号逐一覆盖桌面对应的正常产出和余量产出，不复制报告或预览。

- [ ] **Step 2: Verify delivery structure and hashes**

断言：

```text
BH folders = 11
BOX folders = 7
files per member folder = 3
total DXF files = 54
22 copied BH output hashes match project output
11 BH source hashes still match original source
21 BOX file hashes still match their authoritative project/source locations
```

- [ ] **Step 3: Review final Git status**

Run:

```powershell
git status --short
git diff --check
```

Expected: 仅出现本计划、设计文档、BH 标签实现、BH/BOX 标签测试和源码完整性声明；无空白错误。

### Task 5: 提交并本地合并

**Files:**
- Commit: 本计划、设计文档、BH 标签实现、BH/BOX 标签测试和源码完整性声明。
- Merge target: `main`

**Interfaces:**
- Consumes: Task 1 至 Task 4 的验证通过状态。
- Produces: 本地 `main` 上可复验的 BH 精简标签实现。

- [ ] **Step 1: Commit the verified change**

```powershell
git add src\steel_dxf_split\bh_text.py `
  src\steel_dxf_split\bh_validator.py `
  tests\bh_v152\test_bh_codegen_authority.py `
  tests\bh_v152\test_bh_engineering_regressions.py `
  tests\box_v1\test_writer.py `
  tests\test_bh_v152_package.py `
  tools\verify_bh_v152_source.py `
  docs\superpowers\specs\2026-07-23-bh-label-without-quantity-suffix-design.md `
  docs\superpowers\plans\2026-07-23-bh-label-without-quantity-suffix.md
git commit -m "fix(labels): standardize compact plate labels"
```

- [ ] **Step 2: Merge into local main**

```powershell
git switch main
git merge codex/feature/box-instance-reconstruction-active
```

Expected: fast-forward 或无冲突合并成功；不推送远程。

- [ ] **Step 3: Verify the merged result**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git status --short
```

Expected: 0 failures，工作区干净。
