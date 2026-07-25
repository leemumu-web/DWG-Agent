# Unified BH/BOX Paired Output Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the fused `steel-dxf-split` program so every auto-accepted BH or BOX input publishes exactly one complete normal DXF and one complete weld-allowance DXF, with identical hole geometry/colors and a clean one-command wheel.

**Architecture:** Keep `steel_dxf_split.cli:main` as the only public command and `pipeline.split_dxf` as the only public orchestration seam. Reuse the existing BH v1.5.2 and BOX v1.0.0 domain compilers unchanged, derive the allowance variant from their one saved normal result, validate the pair, and promote one task directory atomically.

**Tech Stack:** Python 3.12, pytest, ezdxf 1.4.4, setuptools backend with offline `uv build`, PowerShell/Linux shell release verification.

## Global Constraints

- Public console scripts contain exactly `steel-dxf-split = steel_dxf_split.cli:main`.
- The runtime must not import or contain `batch_cli.py`, either `weld_allowance_cli.py`, or either `weld_allowance_release.py`.
- The CLI snapshots only top-level DXF files from the input directory; input and output may not be equal or nested.
- Each source is detected once and dispatched to exactly one BH or BOX compiler.
- `normal` and `weld_allowance` are derived from the same native split result; allowance processing must not re-detect, re-split, or re-identify holes.
- Every `auto_accepted/<bh|box>/<member>/` task contains exactly `<member>_normal.dxf` and `<member>_weld_allowance.dxf` as its DXF files.
- A paired-proof failure routes the whole task to `manual_review`; hard I/O or programming failures fail closed with exit code 2 and must not leave an orphan auto-accepted artifact.
- Confirmed mirrored circular holes are left ACI 1 and right ACI 7; unpaired, midline, ambiguous, and non-circular holes remain ACI 7 in both variants.
- Do not modify BH compiler passes, BOX reconstruction/solver logic, Manufacturing IR geometry, or the BH/BOX allowance mathematics.
- The BOX before/after golden directories under `D:\DevData\BOX拆板前后数据` are read-only and remain the geometry authority.

---

### Task 1: Retire stale wrapper execution paths and correct the domain context

**Files:**
- Modify: `tests/test_unified_cli_contract.py`
- Modify: `CONTEXT.md`
- Delete: `scripts/run.ps1`
- Delete: `scripts/run.sh`
- Delete: `scripts/run_bh_pairs.ps1`
- Delete: `scripts/run_bh_pairs.sh`
- Delete: `scripts/run_box_pairs.ps1`

**Interfaces:**
- Consumes: the single console script declared in `pyproject.toml`
- Produces: a repository with no runnable wrapper that calls deleted commands or deleted CLI flags

- [ ] **Step 1: Write the failing static contract test**

Add:

```python
def test_retired_wrapper_scripts_and_context_cannot_restore_old_entrypoints() -> None:
    root = Path(__file__).resolve().parents[1]
    retired = (
        root / "scripts" / "run.ps1",
        root / "scripts" / "run.sh",
        root / "scripts" / "run_bh_pairs.ps1",
        root / "scripts" / "run_bh_pairs.sh",
        root / "scripts" / "run_box_pairs.ps1",
    )
    assert all(not path.exists() for path in retired)
    context = (root / "CONTEXT.md").read_text(encoding="utf-8")
    assert "steel-dxf-split-batch" not in context
    assert "src/steel_dxf_split/batch_cli.py" not in context
    assert "pipeline.py" in context
    assert "normal" in context
    assert "weld_allowance" in context
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_unified_cli_contract.py::test_retired_wrapper_scripts_and_context_cannot_restore_old_entrypoints -q
```

Expected: FAIL because the old wrappers exist and `CONTEXT.md` still declares `steel-dxf-split-batch`.

- [ ] **Step 3: Delete the obsolete wrappers and rewrite the affected `CONTEXT.md` sections**

The corrected execution chain must be:

```text
steel-dxf-split
→ cli.main：只快照输入目录
→ pipeline.split_dxf：单图判型与一次领域拆板
├─ BH：BH v1.5.2 原生核心
└─ BOX：Project2 BOX v1.0.0 核心
→ 同一基础结果派生 normal / weld_allowance
→ 成对验证
→ auto_accepted / manual_review 任务目录原子发布
```

The context must state that direct domain compiler calls are internal verification seams, not production entrypoints.

- [ ] **Step 4: Run the focused test and unified CLI contract**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_unified_cli_contract.py -q
```

Expected: PASS.

### Task 2: Complete paired-output proof coverage

**Files:**
- Modify: `tests/test_paired_output_validation.py`
- Modify: `tests/test_box_gate_integration_v1.py`
- Modify: `tests/test_hole_color_policy_v1.py`
- Modify: `src/steel_dxf_split/paired_output.py`
- Modify: `src/steel_dxf_split/hole_color_policy.py`

**Interfaces:**
- Consumes: `validate_paired_outputs(normal_path, allowance_path, allowance_report_path, family=...)`
- Produces: saved-DXF proof that the pair is bound, complete, unit-compatible, hole/color-identical, and rule-correct

- [ ] **Step 1: Add negative paired-output tests**

Add separate tests that:

```python
with pytest.raises(PairedOutputValidationError, match="not bound"):
    validate_paired_outputs(normal, allowance, wrong_report, family="BH")

with pytest.raises(PairedOutputValidationError, match="contracts differ"):
    validate_paired_outputs(normal, different_units, report, family="BH")

with pytest.raises(PairedOutputValidationError, match="entity sets differ"):
    validate_paired_outputs(normal, missing_entity, report, family="BH")

with pytest.raises(PairedOutputValidationError, match="no verified plate groups"):
    validate_paired_outputs(normal, allowance, empty_groups_report, family="BOX")
```

Each report must be rebound to the exact paths used by that test so the intended validation gate fails first.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_paired_output_validation.py -q
```

Expected: any newly exposed missing validation fails for the stated reason; already implemented gates may pass immediately and require no production change.

- [ ] **Step 3: Add real BOX positive-extension assertions**

In `test_current_release_enables_native_box_production_output`, assert:

```python
assert result.report["paired_output"]["validation"]["ok"] is True
assert result.report["paired_output"]["validation"]["positive_extension_count"] > 0
normal = ezdxf.readfile(result.production_path)
allowance = ezdxf.readfile(result.weld_allowance_path)
assert _box_cut_geometry(normal) == _box_cut_geometry(allowance)
```

Use the existing BOX weld-allowance cut fingerprint helper rather than duplicating geometry rules.

- [ ] **Step 4: Make diagnostic pair ordering geometry-stable**

Extend the order-invariance test to compare accepted pairs after mapping indices back to geometry. If it fails, sort `pairs` by the left and right hole geometry before returning `SymmetricHoleColorPlan`; do not change any color decision.

- [ ] **Step 5: Run BH/BOX pair and color proofs**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_paired_output_validation.py tests\test_hole_color_policy_v1.py tests\bh_v152\test_bh_hole_color_policy.py tests\bh_v152\test_bh_automatic_weld_allowance.py tests\test_box_gate_integration_v1.py tests\box_v1\test_writer.py -q
```

Expected: PASS.

### Task 3: Build and verify the wheel from a clean source snapshot

**Files:**
- Create: `scripts/build_unified_wheel.py`
- Create: `tests/test_unified_wheel_build.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: repository `pyproject.toml`, `README.md`, and `src/`
- Produces: one verified `.whl` copied only after its contents and entry points pass

- [ ] **Step 1: Write a failing clean-wheel build test**

The test runs:

```python
completed = subprocess.run(
    [sys.executable, str(root / "scripts" / "build_unified_wheel.py"), "--output-dir", str(tmp_path)],
    cwd=root,
    check=False,
    text=True,
    capture_output=True,
)
assert completed.returncode == 0, completed.stdout + completed.stderr
```

Then inspect the wheel with `zipfile.ZipFile` and assert:

```python
assert entry_points == (
    "[console_scripts]\n"
    "steel-dxf-split = steel_dxf_split.cli:main\n"
)
assert not any(
    name.endswith(retired)
    for name in names
    for retired in (
        "/batch_cli.py",
        "/weld_allowance_cli.py",
        "/weld_allowance_release.py",
    )
)
assert "steel_dxf_split/paired_output.py" in names
assert "steel_dxf_split/hole_color_policy.py" in names
assert "steel_dxf_split/release_evidence/box_release_attestation.json" in names
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_unified_wheel_build.py -q
```

Expected: FAIL because `scripts/build_unified_wheel.py` does not exist.

- [ ] **Step 3: Implement the clean builder**

`build_unified_wheel.py` must:

1. Parse a required `--output-dir`.
2. Create an OS temporary source directory.
3. Copy only `pyproject.toml`, `README.md`, `uv.lock`, and `src/` into it.
4. Run the already-installed `uv build --offline --no-python-downloads --wheel --no-build-logs --no-create-gitignore --out-dir <temporary-output> <temporary-source>`. The project `.venv` intentionally carries neither `pip` nor the optional PyPA `build` frontend, so the clean builder uses uv's isolated, offline build path without installing into the project environment or accessing the network.
5. Open the one produced wheel and enforce the exact entry-point and retired-module rules above.
6. Copy the verified wheel to a pending file under the requested output directory and atomically replace the final wheel.
7. Never read or reuse repository `build/`, `dist/`, or an earlier release wheel.

- [ ] **Step 4: Update the README release command**

Replace the manual “clear `build/` first” guidance with:

```powershell
.\.venv\Scripts\python.exe scripts\build_unified_wheel.py `
  --output-dir .\release\unified-paired
```

- [ ] **Step 5: Run the wheel test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_unified_wheel_build.py -q
```

Expected: PASS.

### Task 4: Regenerate delivery evidence and run final acceptance

**Files:**
- Generate: `release/unified-paired/steel_dxf_split-1.5.2-py3-none-any.whl`
- Generate: `output/unified-paired-acceptance/auto_accepted/bh/2b1-cb-29/*`
- Generate: `output/unified-paired-acceptance/auto_accepted/box/2b1-cb-56/*`
- Update only through existing generators: `src/steel_dxf_split/release_evidence/box_build_contract.json`
- Update only through existing generators: `src/steel_dxf_split/release_evidence/box_release_attestation.json`

**Interfaces:**
- Consumes: the current source tree, the read-only BH/BOX fixtures, and the BOX release generator
- Produces: an installed-wheel proof and visible BH/BOX paired outputs

- [ ] **Step 1: Run source and golden authority checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_bh_v152_package.py -q
.\.venv\Scripts\python.exe scripts\verify_box_v1_source.py `
  --upstream "D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0"
.\.venv\Scripts\python.exe -m pytest tests\box_v1\test_golden_corpus.py tests\bh_v152\test_bh_corpus_regression.py -q
```

Expected: PASS without modifying either `D:\DevData\BOX拆板前后数据` directory.

- [ ] **Step 2: Regenerate the implementation-bound BOX release evidence**

First verify that `box_build_contract.json` records the current SHA-256 values of
`pyproject.toml` and `uv.lock`. Then run the existing official verifier:

```powershell
.\.venv\Scripts\python.exe scripts\verify_box_v1_fusion.py `
  --inputs "D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf" `
  --references "D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf" `
  --output ".\release\unified-paired\box-v1-fusion-acceptance.json" `
  --manifest ".\samples\box_pairs\box_supervised_manifest.json" `
  --release-gate-output ".\release\unified-paired\box-release-gate.json" `
  --emit-release-attestation ".\src\steel_dxf_split\release_evidence\box_release_attestation.json"
```

Expected: 20/20 pass, unchanged input/reference hashes, and a fresh attestation
that verifies against the current production implementation fingerprint.

- [ ] **Step 3: Build the clean wheel and inspect it**

Run:

```powershell
.\.venv\Scripts\python.exe scripts\build_unified_wheel.py --output-dir .\release\unified-paired
```

Expected: one wheel, one console script, no retired runtime modules.

- [ ] **Step 4: Install the wheel into a temporary virtual environment**

Run the installed `steel-dxf-split` command against a temporary mixed input directory containing `2b1-cb-29_拆板前.dxf` and `2b1-cb-56_拆板前.dxf`, with both source authorizations. Assert exit code 0 and exactly four DXF outputs split between `bh` and `box`.

- [ ] **Step 5: Persist a visible acceptance run**

Use the unified program to write the two sample tasks into `output/unified-paired-acceptance/`. Reopen all four DXFs and verify:

- each task directory contains exactly two DXF files;
- each allowance report is bound to its corresponding normal and allowance paths;
- each pair has a positive extension count;
- normal and allowance hole geometry/colors are identical;
- BH has 24 red and 24 white circular holes in both variants;
- BOX keeps every unpaired real-sample hole white in both variants.

- [ ] **Step 6: Run final static and dynamic verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

Expected: all tests pass, Ruff is clean, and `git diff --check` reports no errors.

### Task 5: Align final user documentation and source-provenance checks

**Files:**
- Replace: `README.md` with the concise unified-entry and paired-output guide
- Modify: `docs/bh/ARCHITECTURE.md`
- Modify: `docs/bh/INPUT_OUTPUT_CONTRACT.md`
- Modify: `docs/bh/REVIEW_WORKFLOW.md`
- Modify: `docs/bh/VALIDATION.md`
- Delete: `scripts/bh/run_bh_samples.sh`
- Delete: `scripts/bh/verify_weld_allowance_release.py`
- Delete: `scripts/build_supervised_release.py`
- Modify: `scripts/verify_box_v1_source.py`
- Modify: `tools/verify_bh_v152_source.py`
- Modify: `tests/box_v1/test_source_provenance.py`
- Modify: `tests/test_box_single_core_route.py`

- [ ] **Step 1: Preserve core authority while documenting the fused runtime**

The public documentation must describe one input-directory command, one native
BH or BOX split per input, deterministic left-red/right-white hole coloring,
paired normal/allowance validation, and atomic task-directory publication. It
must explicitly state that the BH/BOX geometry and allowance mathematics remain
domain-owned and were not merged or rewritten.

- [ ] **Step 2: Remove executable legacy helpers**

Delete scripts that invoke the retired batch or standalone allowance runtime.
Historical provenance remains in Git and in the explicit source-verifier
retirement manifests, not as runnable entrypoints.

- [ ] **Step 3: Close provenance regressions**

The BH and BOX source verifiers must declare the current paired-output/color
patchset, the retired modules, and the exact patched files. Update the stale BOX
provenance test and remove the unused legacy CLI constant import.

- [ ] **Step 4: Verify the final tree and rebuild delivery**

Run the focused provenance tests and Ruff, rebuild the clean wheel so its README
matches the final tree, refresh the installed-wheel acceptance summary against
that exact wheel, then run the complete pytest suite and `git diff --check`.
