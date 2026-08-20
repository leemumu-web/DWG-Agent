# BOX Search Speed and Stable Batch Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the avoidable long stall in BOX face-union search and provide bounded multi-process batch execution while preserving every accepted/review/rejected result and every paired DXF geometry.

**Architecture:** Keep the BOX search contract fail-closed: the 50,000-state budget, completeness flags, candidate ordering, and diagnostics remain unchanged. Before the expensive Shapely union, use the exact Y-span of the selected faces' individual bounds; a union's bounding-box span is exactly the min/max of its members, so subsets outside the target interval can be rejected without changing candidate eligibility. Add opt-in process-level fan-out at the CLI boundary; each drawing remains an isolated `split_classified_dxf` task, the main process alone publishes progress and the BH ledger in input order, and the default worker count stays serial until a full-corpus equivalence gate passes.

**Tech Stack:** Python 3.12, Shapely, `concurrent.futures.ProcessPoolExecutor`, pytest, ezdxf, existing `steel-dxf-split` CLI and paired-output contract.

---

## Task 1: Lock the measured regression and the exact pruning invariant

**Files:**
- Modify: `backend/tests/dxf_splitting/test_box_regressions.py`
- Test: `backend/tests/dxf_splitting/test_box_regressions.py`

- [x] **Step 1: Add a synthetic overlay test that fails before the optimization**

Patch the existing BOX regression module with a three-face stacked projection fixture. Monkeypatch `polygonize_part_projection`, `_source_curves`, and `_assess_candidate`, wrap the module's `unary_union`, and assert that subsets whose exact face-bound span is outside the target interval never reach the expensive union. The test must still assert `states_visited`, `subset_search_complete`, and the candidate tuple for the target face are unchanged. Use the real `Polygon` objects and the real search function; do not replace the search with a fake implementation.

```python
def test_face_union_search_rejects_out_of_span_subsets_before_union(monkeypatch) -> None:
    import steel_dxf_split.box.projection_geometry as geometry

    faces = (
        Polygon(((0, 0), (10, 0), (10, 1), (0, 1))),
        Polygon(((0, 1), (10, 1), (10, 2), (0, 2))),
        Polygon(((0, 2), (10, 2), (10, 3), (0, 3))),
    )
    monkeypatch.setattr(geometry, "polygonize_part_projection", lambda *args, **kwargs: faces)
    monkeypatch.setattr(geometry, "_source_curves", lambda *args, **kwargs: ())
    monkeypatch.setattr(geometry, "_assess_candidate", lambda *args, **kwargs: None)

    original_union = geometry.unary_union
    calls: list[int] = []

    def counted_union(items):
        materialized = tuple(items)
        calls.append(len(materialized))
        return original_union(materialized)

    monkeypatch.setattr(geometry, "unary_union", counted_union)
    result = geometry.search_source_conserving_face_unions(
        (),
        ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=0.0,
            longitudinal_max=10.0,
            transverse_min=0.0,
            transverse_max=3.0,
        ),
        target_transverse_mm=1.0,
        transverse_tolerance_mm=0.01,
        maximum_states=100,
    )

    assert result.subset_search_complete is True
    assert result.states_visited == 6
    # Two empty linework unions plus one maximal-component union are expected;
    # no subset union may be a two-face union.
    assert calls.count(2) == 0
```

- [x] **Step 2: Run the focused test and capture the expected failure**

Run:

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework
backend/.venv/bin/pytest -q backend/tests/dxf_splitting/test_box_regressions.py -k face_union_search_rejects_out_of_span_subsets_before_union
```

Expected before implementation: FAIL because the current loop calls `unary_union` for two-face subsets before checking their span.

## Task 2: Apply exact span pruning inside the bounded BOX search

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/box/projection_geometry.py:2786-3030`
- Test: `backend/tests/dxf_splitting/test_box_regressions.py`

- [x] **Step 1: Cache each face's transverse bounds and carry them with the DFS state**

Immediately after `faces` is validated, create:

```python
face_transverse_bounds = tuple(
    (float(face.bounds[1]), float(face.bounds[3])) for face in faces
)
```

Initialize each stack entry as `(frozenset((seed,)), min_y, max_y)`. When adding a neighbor, push `(next_subset, min(min_y, neighbor_min), max(max_y, neighbor_max))`. Keep the existing `seen` set of `frozenset[int]`, stack order, state budget, and diagnostics unchanged.

- [x] **Step 2: Check the exact span before invoking Shapely union**

Replace the subset-loop body with this equivalent ordering:

```python
subset = stack.pop()
subset_key, min_transverse, max_transverse = subset
if subset_key in seen:
    continue
if len(seen) >= maximum_states:
    state_budget_exhausted = True
    stop = True
    break
seen.add(subset_key)
transverse = max_transverse - min_transverse
if transverse > target_transverse_mm + tolerance:
    continue
if transverse < target_transverse_mm - tolerance:
    neighbors = set().union(*(subset_adjacency[index] for index in subset_key))
else:
    merged = unary_union([faces[index] for index in subset_key])
    if isinstance(merged, Polygon):
        candidate = _assess_candidate(
            merged,
            curves,
            grid_size_mm=grid_size_mm,
            endpoint_tolerance_mm=endpoint_tolerance_mm,
        )
        if candidate is not None:
            retain(candidate)
    neighbors = set().union(*(subset_adjacency[index] for index in subset_key))
for neighbor in sorted(
    neighbors.intersection(component).difference(subset_key),
    reverse=True,
):
    neighbor_min, neighbor_max = face_transverse_bounds[neighbor]
    stack.append(
        (
            subset_key | {neighbor},
            min(min_transverse, neighbor_min),
            max(max_transverse, neighbor_max),
        )
    )
```

The bound is exact because `bounds(unary_union(S)).min_y == min(bounds(face).min_y for face in S)` and likewise for `max_y`; no candidate can satisfy the target tolerance when this interval test fails. For valid, non-empty polygonized faces, use Shapely `coverage_union_all` for target-span exploratory subsets; it is exact for the disjoint polygon coverage produced by `polygonize_part_projection`. When that fast path yields an accepted candidate, recompute only that candidate with the legacy `unary_union` and retain the legacy candidate geometry/order, so published DXF bytes remain stable. If the face-validity certificate is false, use the original `unary_union` path for every subset. Do not change `_assess_candidate`, `retain`, the maximum-state budget, or the completeness result.

- [x] **Step 3: Run the focused regression and the BOX unit suite**

Run:

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework
backend/.venv/bin/pytest -q backend/tests/dxf_splitting/test_box_regressions.py
```

Expected: all tests pass, including the existing 50,000-state contract test; the new test passes and reports no changed candidate/proof semantics.

## Task 3: Add bounded, opt-in process fan-out at the CLI boundary

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/cli.py`
- Modify: `backend/app/platform/config/settings.py`
- Modify: `backend/app/modules/dxf_splitting/adapter.py`
- Test: `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py`
- Test: `backend/tests/infrastructure/test_config.py`

- [x] **Step 1: Add the CLI option without changing its default**

Add `--workers` as an integer argument with default `1` and reject values below 1. Keep the current serial loop for `workers == 1`. For `workers > 1`, submit one picklable worker function per frozen `(input_path, family)` pair to `ProcessPoolExecutor(max_workers=workers)`. The worker calls exactly the existing `split_classified_dxf` once and returns `(input_path, result/error, processing_seconds)`; it must not publish the BH ledger or write progress.

The parent must:

1. collect every future, converting worker exceptions to the current per-drawing `failed` summary;
2. order `results` and `summaries` by the original `inputs` tuple before publishing/printing;
3. update `_verify_quantity_checkpoint` and `_publish_progress` only in the parent, with one increment per completed future;
4. call `publish_bh_project_ledger` once, after all workers finish;
5. leave the output directory contract unchanged (`auto_accepted/<family>/<member>` and `manual_review/<family>/<member>`), relying on unique member names already enforced by `_snapshot_inputs`.

- [x] **Step 2: Expose the fan-out through the real backend adapter, still defaulting to serial**

Add `dxf_split_cli_worker_concurrency: int = Field(default=1, ge=1, le=4)` beside `dxf_split_timeout_seconds`. In `invoke_splitter`, append `--workers` and the configured integer only when it is greater than 1. Use a dedicated `DXF_SPLIT_CLI_WORKER_CONCURRENCY` key: `DXF_SPLIT_WORKER_CONCURRENCY` already controls the Celery queue process count and must not be reused for nested fan-out. Existing deployments and tests with the default keep the exact old command; deployments can explicitly set a bounded value of 2–4 after the corpus gate.

- [x] **Step 3: Add tests for isolation and deterministic ordering**

Use monkeypatched `split_classified_dxf` futures in the CLI test module to make one task finish out of order, assert summaries are restored to input order, the ledger is called exactly once, progress reaches the exact input count, and an exception becomes exactly one failed drawing. Add a settings test asserting the default is `1` and the upper bound rejects `5`.

- [x] **Step 4: Run CLI/backend focused tests**

Run:

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework
backend/.venv/bin/pytest -q \
  backend/tests/dxf_splitting/test_classified_dispatch.py \
  backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py \
  backend/tests/infrastructure/test_config.py
```

Expected: all existing serial contracts pass and the new fan-out tests pass.

## Task 4: Verify single-drawing equivalence before using concurrency

**Files:**
- Create: `tmp_data/workflow_28_speed_20260820/` (generated evidence only; do not add to Git)
- Test: current source DXF `tmp_data/workflow_28_combine_20260820/workflow-28_dxf/BYSJ@零件图@a1-4-cb-85_拆板前.dxf`

- [x] **Step 1: Run `cb-85` with the optimized code in an isolated output directory**

Use the existing pinned CLI contracts and a 360-second timeout. Record wall time, peak RSS, route, report diagnostic, normal/allowance SHA-256, and DXF entity counts in `cb-85-speed.json`.

- [x] **Step 2: Compare the optimized result with the pre-change golden result**

Compare the normal DXF, allowance DXF, and manufacturing fingerprint from `tmp_data/workflow_28_full_run_20260820/complete_run_result.json`. A byte or fingerprint difference is a hard stop; do not enable the optimization for the batch until geometry differences are explained and accepted.

Expected: same route, same report fingerprint, same DXF bytes; materially lower wall time and bounded RSS.

## Task 5: Run the full 326-drawing corpus with bounded multicore execution

**Files:**
- Create: `tmp_data/workflow_28_speed_20260820/` (generated evidence only; do not add to Git)

- [x] **Step 1: Run serial optimized baseline on all 326 drawings**

Run the CLI with `--workers 1` into a fresh output directory and compare the 326 route/fingerprint/normal-DXF/allowance-DXF pairs against `tmp_data/workflow_28_full_run_20260820/`. Stop if any accepted output changes.

- [x] **Step 2: Run the same corpus with two processes**

Run the exact same command with `--workers 2` into a different fresh output directory. Keep process fan-out bounded at two for the first gate; do not run unbounded `os.cpu_count()` fan-out. Record total wall time, per-drawing timings, peak RSS, output counts, routes, and failures.

- [x] **Step 3: Validate production contracts and concurrency invariants**

Validate all output task directories, paired-output reports, `BH拆板信息表.xlsx`, quantity checkpoints, and 326 manifest entries. Compare hashes/fingerprints for every available normal and allowance DXF; list any difference by part number instead of silently accepting it. Confirm no `.steel-dxf-task-*` staging directory remains and no task directory contains files from another drawing.

- [x] **Step 4: Enable backend fan-out only after the two-process gate passes**

The serial and two-process outputs are equivalent and resource limits remain bounded, so the production templates set `DXF_SPLIT_CLI_WORKER_CONCURRENCY=2` with `DXF_SPLIT_WORKER_CONCURRENCY=1` and an 8 GiB worker limit. Never use the Celery queue key for the inner pool; CI explicitly stays at CLI `1` and 2 GiB.

## Task 6: Final regression and delivery report

**Files:**
- Modify: none unless a failing test identifies a concrete defect
- Create: `tmp_data/workflow_28_speed_20260820/speed_audit.json`

- [x] **Step 1: Run all BOX regressions plus the CLI/backend contract tests**

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework
backend/.venv/bin/pytest -q \
  backend/tests/dxf_splitting/test_box_regressions.py \
  backend/tests/dxf_splitting/test_classified_dispatch.py \
  backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py
```

- [x] **Step 2: Write the speed audit with measured before/after facts**

Include `cb-85` baseline/optimized wall time and RSS, serial/2-worker total wall time, exact route/hash/fingerprint comparison counts, and any unresolved manual/failed drawings. Do not report a speedup unless it is measured from the same input and output contract.

- [x] **Step 3: Review the diff and preserve unrelated work**

Run `git diff --check` and `git status --short`; retain the pre-existing BH edits and the new speed changes separately. Do not commit or push until explicitly requested by the user.
