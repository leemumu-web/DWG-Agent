# BOX v2 核心融合实施计划

> **已废弃（2026-07-21）：** 本计划记录“外部 v0.2.1 后端 + legacy 回退”的错误
> 实施路径，不得继续执行。当前计划见
> `2026-07-21-box-v1-source-fusion.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `box-dxf-split v0.2.1` 成为主项目默认 BOX 制造语义核心，同时保留
主项目 release attestation、复核输出、原子提升和 BH 分派。

**Architecture:** 通过 `box_v2_backend.py` 隔离上游类型，以
`box_v2_pipeline.py` 组合新核心与现有外围；legacy BOX 作为显式回退，不参与
v2 结果投票。单图 ProofReport 与版本 release attestation 必须同时通过才进入
`auto_accepted`。

**Tech Stack:** Python 3.12/3.13、ezdxf 1.4.4、Shapely 2.1、
box-dxf-split 0.2.1、pytest 8.4。

## Global Constraints

- 权威金样目录只读，禁止修改、移动或重命名。
- 生产运行时不得读取拆板后 DXF。
- 上游依赖固定为 commit
  `b7b47f33cec1b8c2ae881badc4400cd57d136d2d`，不使用浮动分支。
- `box_backend=v2` 不得在失败后自动 fallback 到 legacy。
- BH 运行时不得导入 `box_dxf_split`。
- 不安装依赖；依赖同步必须另获用户明确许可。
- 不提交、不推送；每个任务以 diff/test checkpoint 代替 commit。
- 遵循 RED → GREEN → REFACTOR；没有先观察到预期失败，不写生产实现。

---

### Task 1: 固化依赖和来源合同

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/steel_dxf_split/pipeline.py`
- Modify: `src/steel_dxf_split/cli.py`
- Test: `tests/test_box_v2_contract.py`

**Interfaces:**

- Produces:
  `BoxSourceContract.validate() -> None`；
  `SplitOptions.box_backend: Literal["v2", "legacy"]`；
  `SplitOptions.box_source_contract: BoxSourceContract | None`。

- [ ] **Step 1: Write the failing contract tests**

```python
def test_box_source_contract_accepts_only_the_pinned_tekla_profile() -> None:
    BoxSourceContract().validate()
    with pytest.raises(ValueError, match="source contract"):
        BoxSourceContract(export_profile="other").validate()


def test_split_options_select_v2_without_silent_source_authorization() -> None:
    options = SplitOptions()
    assert options.box_backend == "v2"
    assert options.box_source_contract is None
```

- [ ] **Step 2: Run the test and verify RED**

```powershell
$env:PYTHONPATH='src;D:\anaconda3\Lib\site-packages'
.\.venv\Scripts\python.exe -m pytest `
  -p no:cacheprovider -o addopts= -q tests/test_box_v2_contract.py
```

Expected: collection fails because `BoxSourceContract` and the new option fields do not
exist.

- [ ] **Step 3: Implement the minimal contract**

Add to `pipeline.py`:

```python
BOX_V2_EXPORT_PROFILE = "project_tekla_box_dxf_v1"


@dataclass(frozen=True, slots=True)
class BoxSourceContract:
    source_system: str = "tekla_structures"
    drawing_kind: str = "single_part_drawing"
    member_family: str = "welded_box"
    export_profile: str = BOX_V2_EXPORT_PROFILE

    def validate(self) -> None:
        expected = (
            self.source_system == "tekla_structures"
            and self.drawing_kind == "single_part_drawing"
            and self.member_family == "welded_box"
            and self.export_profile == BOX_V2_EXPORT_PROFILE
        )
        if not expected:
            raise ValueError("BOX source contract violation.")
```

Extend `SplitOptions` with `box_backend="v2"` and
`box_source_contract: BoxSourceContract | None = None`. Add the CLI authorization flag
and construct the contract only when the exact profile value is supplied.

Add the pinned dependency to `pyproject.toml`:

```toml
"box-dxf-split @ git+https://github.com/Creeken-Harrans/box-dxf-split.git@b7b47f33cec1b8c2ae881badc4400cd57d136d2d",
```

Run `uv lock` only; do not run `uv sync`.

- [ ] **Step 4: Verify GREEN and lock determinism**

Run the Task 1 test, then:

```powershell
uv lock --check
git diff --check
```

- [ ] **Step 5: Checkpoint**

Inspect `git diff -- pyproject.toml uv.lock src/steel_dxf_split/pipeline.py
src/steel_dxf_split/cli.py tests/test_box_v2_contract.py`. Do not commit.

### Task 2: Make profile routing order-independent

**Files:**

- Modify: `src/steel_dxf_split/profile_detection.py`
- Test: `tests/test_profile_detection_unique_v1.py`

**Interfaces:**

- Produces:
  `ProfileFamilyConflictError(ValueError)`；
  `detect_profile_family(doc) -> str | None` with complete evidence collection.

- [ ] **Step 1: Write failing tests**

Create drawings containing BOX only, BH only, both in each entity order, and neither:

```python
@pytest.mark.parametrize("values", [
    ("BOX600*500*20*25", "BH600*300*12*20"),
    ("BH600*300*12*20", "BOX600*500*20*25"),
])
def test_mixed_profile_families_fail_independent_of_entity_order(values) -> None:
    doc = ezdxf.new()
    for index, value in enumerate(values):
        doc.modelspace().add_text(value, dxfattribs={"insert": (index, 0)})
    with pytest.raises(ProfileFamilyConflictError):
        detect_profile_family(doc)
```

- [ ] **Step 2: Verify RED**

Expected: both mixed cases currently return the first matched family.

- [ ] **Step 3: Implement complete evidence collection**

Collect matches into `families: set[str]`; return only when its size is zero or one.
Raise `ProfileFamilyConflictError` with sorted family names when size is greater than one.

- [ ] **Step 4: Verify GREEN**

Run the new tests and `tests/test_box_architecture_v2.py`.

- [ ] **Step 5: Checkpoint**

Run `git diff --check`; do not commit.

### Task 3: Add the deep BOX v2 backend adapter

**Files:**

- Create: `src/steel_dxf_split/box_v2_backend.py`
- Test: `tests/test_box_v2_backend.py`

**Interfaces:**

- Consumes:
  `BoxSourceContract`；
  upstream `build_source_ir()`、`resolve_box_metadata()`、
  `solve_complete_box()`、`validate_manufacturing_ir()`。
- Produces:

```python
@dataclass(frozen=True, slots=True)
class BoxV2Compilation:
    part_number: str
    drawing_scale: float
    proof_disposition: str
    manufacturing_fingerprint: str
    report: dict[str, object]
    review_assembly: SplitAssembly
    _mir: object = field(repr=False, compare=False)
```

The module exports
`compile_box_v2(input_path: Path, *, source_contract: BoxSourceContract) ->
BoxV2Compilation` and
`write_box_v2_production(compilation: BoxV2Compilation, output_path: Path) ->
dict[str, object]`.

- [ ] **Step 1: Write a failing real-sample test**

Use `samples/box_pairs/BOX_拆板前_dxf/2b1-cb-56_拆板前.dxf`. Assert four physical
roles, `auto_accept`, MIR validation, non-empty fingerprint, and
`ground_truth_used_for_decision is False`.

- [ ] **Step 2: Verify RED**

Run with the local upstream checkout on `PYTHONPATH`:

```powershell
$env:PYTHONPATH='src;D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0\src;D:\anaconda3\Lib\site-packages'
.\.venv\Scripts\python.exe -m pytest `
  -p no:cacheprovider -o addopts= -q tests/test_box_v2_backend.py
```

Expected: import fails because `box_v2_backend.py` does not exist.

- [ ] **Step 3: Implement compile and report mapping**

The adapter validates the contract before reading the file, runs the upstream core passes,
requires MIR validation `ok=True`, and returns a report containing backend version/commit,
metadata, ProofReport, search status, MIR fingerprint and source fingerprints.

For the review adapter, call upstream `layout_box_manufacturing_ir()` and
`contour_vertices()`, map upstream roles to current `PlateRole`, and create current
`DrawingMetadata`/`Plate` values. This `SplitAssembly` is never used for production
authorization.

- [ ] **Step 4: Add production writer closure**

Write a second failing test, then call upstream
`write_box_clean(compilation._mir, output_path,
purpose=OutputPurpose.PRODUCTION)` and
`validate_saved_dxf(output_path, compilation._mir, layout=layout)`. Reject any saved report
with `ok is not True`.

- [ ] **Step 5: Verify GREEN and upstream core compatibility**

Run the new tests plus upstream core tests for source, metadata, assembly, equivalence,
writer and validator with current `src` first on `PYTHONPATH`.

- [ ] **Step 6: Checkpoint**

Run `git diff --check`; do not commit.

### Task 4: Compose the v2 pipeline with the existing outer delivery

**Files:**

- Create: `src/steel_dxf_split/box_v2_pipeline.py`
- Modify: `src/steel_dxf_split/pipeline.py`
- Modify: `src/steel_dxf_split/cli.py`
- Test: `tests/test_box_v2_pipeline.py`
- Modify: legacy route tests to pass `box_backend="legacy"` explicitly.

**Interfaces:**

- Consumes: `compile_box_v2()`、`write_box_v2_production()`、
  `load_verified_box_release_attestation()` and current review/sheet writers.
- Produces:
  `split_box_v2_dxf(input_path, output_dir, options) -> SplitResult`.

- [ ] **Step 1: Write failing routing tests**

Cover:

1. missing source contract → error and zero artifacts；
2. auto-accepted single-file proof without release → review only；
3. valid release → production REGION only；
4. `require_auto_accept=True` without release → error and zero artifacts；
5. explicit `box_backend="legacy"` still calls the legacy pipeline.

- [ ] **Step 2: Verify RED**

Expected: v2 dispatch and module do not exist.

- [ ] **Step 3: Implement v2 staging and routing**

Copy no solver logic. The function:

1. validates source contract；
2. compiles once with v2；
3. loads release attestation if supplied；
4. computes the two-layer route；
5. writes all requested files into a same-drive `TemporaryDirectory`；
6. validates every saved output；
7. serializes `BOX-COMPILATION-REPORT-3.0`；
8. uses the existing `_promote_staged_files()` transaction only after all checks pass.

No exception path may call the legacy solver.

- [ ] **Step 4: Route the common entry**

In `pipeline.split_dxf()`:

```python
if family == "BOX":
    if options.box_backend == "legacy":
        from .box_pipeline import split_box_dxf
        return split_box_dxf(input_path, output_dir, options)
    if options.box_backend == "v2":
        from .box_v2_pipeline import split_box_v2_dxf
        return split_box_v2_dxf(input_path, output_dir, options)
    raise ValueError("Unsupported BOX backend.")
```

- [ ] **Step 5: Verify GREEN**

Run the new pipeline tests. Run all modified legacy pipeline nodes with
`box_backend="legacy"` and confirm their previous assertions remain unchanged.

- [ ] **Step 6: Checkpoint**

Inspect the route diff and run `git diff --check`; do not commit.

### Task 5: Make release fingerprints cover the actual v2 core

**Files:**

- Modify: `src/steel_dxf_split/box_release.py`
- Modify: `tests/test_box_release_attestation_v1.py`
- Modify: `tests/test_box_architecture_v2.py`

**Interfaces:**

- Produces:
  `production_implementation_fingerprint()` that hashes both main-project production files
  and the installed pinned upstream core files.

- [ ] **Step 1: Write a failing fingerprint coverage test**

Assert the source list contains `box_v2_backend.py`, `box_v2_pipeline.py` and upstream
`assembly.py`, `manufacturing_ir.py`, `writer.py`, while excluding preview, offline
comparison and manual references.

- [ ] **Step 2: Verify RED**

Expected: current fingerprint only hashes legacy `steel_dxf_split` files.

- [ ] **Step 3: Implement deterministic multi-package hashing**

Resolve `box_dxf_split.__file__` without importing its preview pipeline. Hash the fixed
production module allowlist using repository-relative logical names and file bytes. Include
the installed upstream `__version__` and the expected commit identifier in the canonical
payload.

- [ ] **Step 4: Verify old attestation invalidation**

Create an attestation, alter one copied upstream module in an isolated temporary package,
and assert load rejects implementation drift. Do not mutate either real repository.

- [ ] **Step 5: Verify GREEN**

Run release, architecture and v2 pipeline tests.

- [ ] **Step 6: Checkpoint**

Run `git diff --check`; do not commit.

### Task 6: Double-corpus acceptance and production certification

**Files:**

- Create: `scripts/verify_box_v2_fusion.py`
- Create: `docs/superpowers/reports/2026-07-20-box-v2-fusion-validation.md`
- Test: `tests/test_box_v2_fusion_verifier.py`

**Interfaces:**

- Produces one JSON summary with:
  gold directory integrity before/after、20-pair manufacturing comparison、
  Project2 30-file compile status、saved REGION validation、label diagnostics and release
  fingerprints.

- [ ] **Step 1: Write failing verifier tests**

Use injected paths under `tmp_path` to test missing files, name mismatch, read-only hash
snapshot comparison and non-zero exit on any failed corpus item.

- [ ] **Step 2: Verify RED**

Expected: verifier module does not exist.

- [ ] **Step 3: Implement the read-only verifier**

The verifier must:

1. snapshot SHA-256、length and `mtime_ns` for both authoritative directories；
2. compile all 20 before files with v2；
3. compare against corresponding after files using the frozen curve-aware comparator；
4. compile all 30 Project2 inputs；
5. write outputs only under a newly created OS temp directory；
6. snapshot gold directories again and require zero changes；
7. emit machine JSON and a Chinese Markdown summary.

- [ ] **Step 4: Run Project2 and gold acceptance**

Expected:

```text
gold manufacturing: 20/20
project2 single-file proof: 30/30 auto_accept
failed/rejected: 0
gold files changed: 0
```

Record the four known exact side-label diagnostics separately from manufacturing verdict.

- [ ] **Step 5: Generate a fresh release attestation and production run**

Use only the verified JSON fingerprints to create the new attestation under OS temp. Run all
20 before files through the main v2 production entry and require saved REGION validation for
every file.

- [ ] **Step 6: Checkpoint**

Review the report and machine JSON; do not commit.

### Task 7: Full regression, lint and handoff

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/reports/2026-07-20-box-v2-fusion-validation.md`

- [ ] **Step 1: Update user-facing documentation**

Document the v2 default, exact source-contract CLI flag, explicit legacy fallback, two-layer
authorization, pinned upstream commit and known side-label/solver-budget/inner-contour
boundaries.

- [ ] **Step 2: Run all BOX tests in bounded workers**

Use `scripts/pytest_worker.py` per slow node so native finalizers cannot hide test status.
Expected: zero failed assertions.

- [ ] **Step 3: Run all BH and full-project tests**

Record any pre-existing environment-only failure separately. No new BOX or BH assertion
failure is allowed.

- [ ] **Step 4: Run lint, import and packaging checks**

```powershell
uv lock --check
.\.venv\Scripts\python.exe -m compileall -q src
git diff --check
```

If ruff/mypy are available without installation, run them; otherwise record the missing tool
without claiming it passed.

- [ ] **Step 5: Verify scope and immutable data**

Compare final gold snapshots, inspect `git status --short`, and confirm no file outside this
worktree and OS temp was written.

- [ ] **Step 6: Handoff**

Report modified files, test counts, corpus results, remaining risks and the fact that no
commit/push occurred.
