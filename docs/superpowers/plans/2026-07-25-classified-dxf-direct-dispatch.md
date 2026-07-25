# Classified DXF Direct Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove DXF profile re-detection and dispatch every frozen BH/BOX classification directly to its matching Steel DXF Split 1.5.2 domain core.

**Architecture:** The workflow writes an exact classified-input manifest beside the frozen DXFs. The Stage CLI validates that manifest against the directory snapshot, passes its explicit family into a domain-direct pipeline entry, and preserves the existing paired-output, progress, review, validation, and persistence contracts.

**Tech Stack:** Python 3.12, FastAPI worker services, SQLAlchemy, pytest, `steel_dxf_split` 1.5.2, ezdxf, MySQL, MinIO.

---

## Task 1: Lock the classified-input contract with failing tests

**Files:**
- Modify: `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/cli.py`

- [ ] **Step 1: Add a test that invokes the CLI parser without a classification manifest**

```python
with pytest.raises(SystemExit):
    split_cli.build_parser().parse_args(
        ["input", "--output-dir", "output"]
    )
```

- [ ] **Step 2: Add manifest validation tests**

Cover an exact mixed BH/BOX mapping, a missing input, an extra manifest item, a duplicate
name, a nested path, and an unsupported `BT` family. Each invalid case must raise a
`ValueError` containing the offending field or file name.

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```bash
cd backend
uv run pytest -q tests/dxf_splitting/test_dxf_splitting_pipeline.py -k classified_manifest
```

Expected: failures because the manifest loader and parser option do not exist.

- [ ] **Step 4: Implement the strict manifest loader**

Add:

```python
CLASSIFIED_INPUT_SCHEMA = "STEEL-DXF-CLASSIFIED-SPLIT-INPUT-1.0"

def _load_classified_inputs(
    path: Path,
    inputs: tuple[Path, ...],
) -> dict[Path, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"schema", "items"}:
        raise ValueError("分类清单顶层字段无效。")
    if payload["schema"] != CLASSIFIED_INPUT_SCHEMA:
        raise ValueError("分类清单 schema 无效。")
    return _validate_classified_items(payload["items"], inputs)
```

Implement `_validate_classified_items` in the same file. It must reject unknown fields,
unsafe names, non-DXF names, families outside `{"BH", "BOX"}`, duplicates, and any
non-bijective mapping.

- [ ] **Step 5: Run the focused tests**

Run the same pytest command. Expected: all `classified_manifest` tests pass.

## Task 2: Replace internal profile detection with explicit dispatch

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pipeline.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/cli.py`
- Delete: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/profile_detection.py`
- Modify: `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`

- [ ] **Step 1: Add a failing direct-dispatch test**

Patch `split_bh_dxf` and `compile_box` independently. Assert that:

```python
split_classified_dxf(source, output, options, family="BH")
```

calls BH exactly once and never calls BOX, while `family="BOX"` calls BOX exactly once
and never calls BH. An unsupported family must fail before either core is called.

- [ ] **Step 2: Run the direct-dispatch tests**

Expected: fail because `split_classified_dxf` does not exist.

- [ ] **Step 3: Implement the explicit-family entry**

Rename `split_dxf` to:

```python
def split_classified_dxf(
    input_path: str | Path,
    output_dir: str | Path,
    options: SplitOptions,
    *,
    family: str,
) -> SplitResult:
```

Remove `load_document`, `gc`, and `detect_profile_family`. Validate family and its source
contract before creating output directories, then call only the selected core.

- [ ] **Step 4: Route CLI entries using the classified manifest**

For each frozen path:

```python
result = split_classified_dxf(
    input_path,
    args.output_dir,
    options,
    family=classified_inputs[input_path],
)
```

Require `--classification-manifest` in normal execution and retain progress and per-file
failure isolation.

- [ ] **Step 5: Delete the detector and prove it is unreachable**

Run:

```bash
rg -n "profile_detection|detect_profile_family" Stages/steel_dxf_split_v1.5.2 backend
```

Expected: no production or test references.

## Task 3: Generate the manifest from frozen workflow classification

**Files:**
- Modify: `backend/app/modules/dxf_splitting/adapter.py`
- Modify: `backend/app/modules/dxf_splitting/execution.py`
- Modify: `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`

- [ ] **Step 1: Add a failing adapter command test**

Call `invoke_splitter` with an explicit manifest path and assert the subprocess command
contains:

```text
--classification-manifest <absolute manifest path>
```

- [ ] **Step 2: Add a failing workflow manifest test**

Use a mixed BH/BOX fixture and assert the written JSON maps every staged file to the
persisted classification `part_type`, with no DXF-derived inference.

- [ ] **Step 3: Implement atomic manifest writing**

Write a manifest from `StagedSplitSource` values with schema
`STEEL-DXF-CLASSIFIED-SPLIT-INPUT-1.0`. Preserve the already-validated original file
name and exact `semantic.part_type`.

- [ ] **Step 4: Pass the manifest through the adapter**

Extend:

```python
invoke_splitter(
    input_directory,
    output_directory,
    *,
    classification_manifest: Path,
    expected_input_count: int,
    progress_callback=publish_progress,
)
```

Resolve and validate the file before starting the subprocess.

- [ ] **Step 5: Run adapter and workflow tests**

Expected: the mixed batch passes and the fake process observes the manifest option.

## Task 4: Update independent validation semantics

**Files:**
- Modify: `backend/app/modules/dxf_splitting/validation.py`
- Modify: `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`

- [ ] **Step 1: Add a failing mismatch test**

Provide a classified BH source with a BOX native result. Assert the item becomes manual
review with a diagnostic stating that the domain core report conflicts with the frozen
classification.

- [ ] **Step 2: Rename detection-oriented checks**

Replace `family_detected_from_dxf` with `domain_report_family_supported` and replace
“拆板识别族” wording with “拆板领域报告类型”. Keep the equality check against the
classification type.

- [ ] **Step 3: Run validation tests**

Expected: matching BH/BOX results pass; mismatches are explicit manual-review findings.

## Task 5: Rebuild BOX release evidence from the full frozen corpus

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/box/release.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/release_evidence/box_release_attestation.json`
- Modify: `Stages/steel_dxf_split_v1.5.2/README.md`

- [ ] **Step 1: Remove the deleted detector from the protected file set**

Ensure `production_implementation_payload()` contains the explicit dispatch pipeline and
CLI, but no `profile_detection.py`.

- [ ] **Step 2: Restore the complete upstream release harness in a temporary directory**

Use commit `a48cc85cbeca7f205ccc8cbc14ec8e985929c0d3` as the source for the 20 frozen BOX
pairs and release scripts. Overlay the current runtime source only in that temporary
verification tree.

- [ ] **Step 3: Run all 20 paired acceptance cases**

Expected: 20/20 pass, split 10 calibration and 10 acceptance, with the ground-truth
firewall true.

- [ ] **Step 4: Emit and verify the new attestation**

Generate the attestation through `write_box_release_attestation`, copy only the verified
attestation into the slim runtime, and immediately load it with
`load_verified_box_release_attestation()`.

- [ ] **Step 5: Run a real BH and BOX CLI smoke**

Supply a classified manifest for each real sample. Expected: no implementation drift;
each accepted sample produces exactly a normal and weld-allowance DXF pair.

## Task 6: Close orphan split runs

**Files:**
- Modify: `backend/app/modules/dxf_splitting/persistence.py`
- Modify: `backend/app/modules/workflows/routes/commands.py`
- Modify: `backend/app/modules/jobs/recovery.py`
- Modify: `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`

- [ ] **Step 1: Add cancellation and restart recovery tests**

Create a `running` split run whose Job is cancelled or terminal. Assert cancellation and
startup reconciliation set the run to `interrupted`, set `finished_at`, and preserve
already recorded counters.

- [ ] **Step 2: Implement one idempotent reconciliation function**

The function must lock the run, verify the associated Job attempt, and close only
orphaned `running` rows. Calling it twice must not change a terminal row.

- [ ] **Step 3: Call reconciliation from existing lifecycle owners**

Call it from `backend/app/modules/workflows/routes/commands.py::cancel_workflow_api`
after the bound Job is transitioned to cancelled, for immediate consistency. Call it
from `backend/app/modules/jobs/recovery.py::reconcile_stale_running_jobs` after each
stale Job transition, for process-death consistency.

- [ ] **Step 4: Run lifecycle tests**

Expected: no cancelled Job retains a visible `running` split run.

## Task 7: Full verification and production E2E

**Files:**
- Modify: `backend/tests/dxf_splitting/README.md`
- Modify: `Stages/steel_dxf_split_v1.5.2/README.md`

- [ ] **Step 1: Run focused and full automated gates**

```bash
cd backend
uv run pytest -q tests/dxf_splitting tests/workflows
uv run ruff check app/modules/dxf_splitting tests/dxf_splitting
cd ..
bash -n scripts/*.sh scripts/lib/*.sh
```

Expected: all commands pass.

- [ ] **Step 2: Restart only affected services and check migrations**

Confirm backend and DXF split worker are healthy and the database is at Alembic head.

- [ ] **Step 3: Submit a real mixed classified batch through HTTP**

Use the official workflow endpoint. Confirm the Job is consumed by the real worker and
the run reaches `completed` or a geometry-justified `completed_with_review`, never a
release-attestation failure.

- [ ] **Step 4: Inspect persisted outputs**

Download and reopen every formal DXF and JSON report. Confirm file IDs, object-store
objects, database rows, classification type, domain report type, and UI presentation
agree.

- [ ] **Step 5: Measure successful performance**

Record wall-clock processing time, files/minute, worker peak RSS, and per-file compiler
versus preview time. Performance acceptance must use successful splitting outputs.

- [ ] **Step 6: Commit and push**

Stage only files owned by this plan, verify the diff contains no unrelated concurrent
changes, commit to `main`, push, and verify `origin/main` equals local `HEAD`.
