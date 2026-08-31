# PL Professional Edge Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make independent PL output preserve professional edge slopes and remove redundant collinear LINE splits while retaining the confirmed length, label, and safety contracts.

**Architecture:** Keep longitudinal proof and piecewise X growth unchanged as the authority for carrier selection. Add two small output-boundary corrections inside `pl/development.py`: simplify degree-2 collinear LINE nodes across source provenance, and normalize an unambiguous pair of nearly parallel terminal LINEs without changing the extremal target-length endpoint. Extend the 21-part and 122-pair corpus tests from bounding-box checks to ordered edge/detail checks.

**Tech Stack:** Python 3.12, ezdxf, Shapely, pytest, Ruff.

## Global Constraints

- Modify only `steel_dxf_split.pl`, PL tests, and PL documentation; do not change BH, BOX, merge-image, or shared output behavior.
- `K_FACTOR` remains exactly `0.5`.
- Target length remains `max(projection, K length, BOM)` rounded upward to exactly `0.1 mm`; never round downward.
- The only label is `p=<part number>` with the existing adaptive `10–30 mm` height contract.
- Non-carrier dimensions and corresponding endpoint positions use `0.1 mm` tolerance; normalized terminal direction components use `1e-6` tolerance.
- Production code must not contain sample part names, sample paths, or coordinate special cases.
- Do not overwrite user inputs, push a remote branch, or publish a replacement result directory during implementation.

---

### Task 1: Capture both reported defects at the public transform and corpus boundaries

**Files:**
- Modify: `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`
- Modify: `backend/tests/dxf_splitting/test_pl_real_corpus.py`

**Interfaces:**
- Consumes: `transform_outline(...) -> tuple[tuple[DXFEntity, ...], DevelopmentMetrics]` and the existing environment-gated 21-part corpus.
- Produces: two RED regressions named `test_cross_source_collinear_courses_are_one_output_line` and `test_professional_terminal_slopes_are_preserved`.

- [ ] **Step 1: Add an anonymous fragmented rectangle regression**

Add this helper and test beside the existing `test_slanted_end_station_does_not_remain_as_an_output_line_split`:

```python
def _fragmented_rectangle() -> tuple[tuple[DXFEntity, ...], Polygon]:
    document = ezdxf.new()
    modelspace = document.modelspace()
    courses = (
        ((0.0, 350.0), (800.0, 350.0)),
        ((800.0, 350.0), (1000.0, 350.0)),
        ((1000.0, 350.0), (1000.0, 0.0)),
        ((1000.0, 0.0), (800.0, 0.0)),
        ((800.0, 0.0), (0.0, 0.0)),
        ((0.0, 0.0), (0.0, 350.0)),
    )
    entities = tuple(
        modelspace.add_line(start, end, dxfattribs={"layer": "Part"})
        for start, end in courses
    )
    return entities, Polygon(((0.0, 0.0), (1000.0, 0.0), (1000.0, 350.0), (0.0, 350.0)))


def test_cross_source_collinear_courses_are_one_output_line() -> None:
    entities, polygon = _fragmented_rectangle()
    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=35.0)

    transformed, metrics = transform_outline(
        entities,
        longitudinal=proof,
        projection_length_mm=1000.0,
        k_length_mm=1100.0,
        bom_length_mm=1100.0,
        anchor_x_mm=0.0,
    )

    assert metrics.target_length_mm == pytest.approx(1100.0)
    assert tuple(entity.dxftype() for entity in transformed) == ("LINE",) * 4
    assert validate_closed_outline(transformed).equals_exact(
        Polygon(((0.0, 0.0), (1100.0, 0.0), (1100.0, 350.0), (0.0, 350.0))),
        0.001,
    )
```

- [ ] **Step 2: Add a professional terminal-direction helper and corpus assertion**

Add the imports `hypot` and `DXFEntity`, then add:

```python
def _terminal_directions(entities: tuple[DXFEntity, ...]) -> tuple[tuple[float, float], ...]:
    lines = tuple(entity for entity in entities if entity.dxftype() == "LINE")
    points = tuple(
        (float(point.x), float(point.y))
        for entity in lines
        for point in (entity.dxf.start, entity.dxf.end)
    )
    height = max(point[1] for point in points) - min(point[1] for point in points)
    terminals: list[tuple[float, tuple[float, float]]] = []
    for entity in lines:
        start = entity.dxf.start
        end = entity.dxf.end
        dx = float(end.x - start.x)
        dy = float(end.y - start.y)
        if abs(dy) < height - 0.1:
            continue
        if dy < 0.0:
            dx = -dx
            dy = -dy
        length = hypot(dx, dy)
        terminals.append(
            ((float(start.x) + float(end.x)) / 2.0, (dx / length, dy / length))
        )
    assert len(terminals) == 2
    return tuple(direction for _, direction in sorted(terminals))
```

Inside the 21-part test, after loading each saved DXF, load its professional reference and compare terminal directions only when both files yield one unambiguous pair. The explicit `z2-cb-79` assertion must be:

```python
professional = ezdxf.readfile(reference_dir / "z2-cb-79.dxf")
generated = ezdxf.readfile(items_by_part["z2-cb-79"]["output"]["path"])
expected = _terminal_directions(tuple(professional.modelspace().query("LINE")))
actual = _terminal_directions(tuple(generated.modelspace().query("LINE")))
assert actual == pytest.approx(expected, abs=1e-6)
```

- [ ] **Step 3: Run the two focused regressions and record RED**

Run:

```powershell
$env:PYTHONPATH='Stages/steel_dxf_split_v1.5.2/src'
$env:PL_REAL_SOURCE_DXF='E:/桌面/DWG-Agent/.tmp/pl_merge_dxf_20260822/merge.dxf'
$env:PL_REAL_REFERENCE_DIR='E:/桌面/DWG-Agent/.tmp/pl_dxf_batch_20260822'
& 'C:/Users/李某/AppData/Local/Temp/codex-pl-splitter-py312/Scripts/python.exe' -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_real_corpus.py -k 'cross_source_collinear or professional_terminal' -q
```

Expected: two failures. The fragmented rectangle has 6 LINE entities instead of 4, and one generated terminal direction differs from the professional direction by more than `1e-6`.

- [ ] **Step 4: Commit the RED tests**

```powershell
git add backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_real_corpus.py
git commit -m "test: capture PL professional edge defects"
```

### Task 2: Merge safe collinear output LINEs across source provenance

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py:422-492`
- Test: `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`

**Interfaces:**
- Consumes: `_NativePiece(source_index: int, entity: DXFEntity)` after `_transform_groups`.
- Produces: `_coalesce_output_lines(pieces) -> tuple[_NativePiece, ...]` with no redundant degree-2 collinear LINE node, regardless of original source index.

- [ ] **Step 1: Remove the provenance-only merge restriction**

Replace the nested-pair guard in `_coalesce_output_lines` with:

```python
        for first_index, first in enumerate(result):
            for second_index in range(first_index + 1, len(result)):
                second = result[second_index]
                merged = _merge_collinear_lines(first.entity, second.entity)
                if merged is not None:
                    merged_pair = (
                        first_index,
                        second_index,
                        _NativePiece(first.source_index, merged),
                    )
                    break
```

The input already comes from `canonical_boundary_pieces`, and `validate_closed_outline` remains the postcondition that rejects a branch, gap, or changed material ring.

- [ ] **Step 2: Run focused coalescing tests**

Run:

```powershell
& $py -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -k 'cross_source_collinear or slanted_end_station or overlapping_collinear or competing_overlap' -q
```

Expected: all selected tests pass; the anonymous rectangle now has 4 LINE entities, while overlap and competing-cycle protections remain green.

- [ ] **Step 3: Commit the coalescing fix**

```powershell
git add Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py
git commit -m "fix: simplify PL collinear output courses"
```

### Task 3: Preserve the canonical slope of an unambiguous terminal pair

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py`
- Test: `backend/tests/dxf_splitting/test_pl_real_corpus.py`

**Interfaces:**
- Consumes: transformed `_NativePiece` values and `LongitudinalProof.intervals[*].upper_entity_indices/lower_entity_indices`.
- Produces: `_normalize_terminal_pair(pieces, longitudinal) -> tuple[_NativePiece, ...]`.

- [ ] **Step 1: Add endpoint utilities that mutate only copied LINE entities**

Add:

```python
def _oriented_line_points(entity: DXFEntity) -> tuple[object, object]:
    line = cast(Line, entity)
    start = line.dxf.start
    end = line.dxf.end
    return (start, end) if float(start.y) <= float(end.y) else (end, start)


def _move_line_node(
    pieces: tuple[_NativePiece, ...],
    old_x: float,
    old_y: float,
    new_x: float,
) -> tuple[_NativePiece, ...]:
    moved: list[_NativePiece] = []
    matched = 0
    for piece in pieces:
        entity = piece.entity.copy()
        for attribute in ("start", "end"):
            if entity.dxftype() != "LINE":
                continue
            point = getattr(entity.dxf, attribute)
            if hypot(float(point.x) - old_x, float(point.y) - old_y) <= _DIMENSION_TOLERANCE_MM:
                setattr(entity.dxf, attribute, (new_x, old_y, float(point.z)))
                matched += 1
        moved.append(_NativePiece(piece.source_index, entity))
    if matched != 2:
        raise _station_split_error("端边斜率校正节点没有唯一连接两条原生 LINE。")
    return tuple(moved)
```

- [ ] **Step 2: Normalize only one unambiguous nearly parallel terminal pair**

Add `_normalize_terminal_pair`. It must exclude every source index referenced by upper/lower interval courses, require exactly two remaining LINE candidates, orient both from lower Y to upper Y, sort by midpoint X, and return unchanged unless their vertical spans agree within `0.1 mm` and their horizontal-vector difference is positive but at most `0.1 mm`:

```python
def _normalize_terminal_pair(
    pieces: tuple[_NativePiece, ...],
    longitudinal: LongitudinalProof,
) -> tuple[_NativePiece, ...]:
    course_indices = {
        source_index
        for interval in longitudinal.intervals
        for source_index in (*interval.upper_entity_indices, *interval.lower_entity_indices)
    }
    candidates = tuple(
        piece
        for piece in pieces
        if piece.source_index not in course_indices and piece.entity.dxftype() == "LINE"
    )
    if len(candidates) != 2:
        return pieces
    ordered = sorted(
        candidates,
        key=lambda piece: sum(float(point.x) for point in _oriented_line_points(piece.entity)),
    )
    left_lower, left_upper = _oriented_line_points(ordered[0].entity)
    right_lower, right_upper = _oriented_line_points(ordered[1].entity)
    left_dx = float(left_upper.x - left_lower.x)
    right_dx = float(right_upper.x - right_lower.x)
    left_dy = float(left_upper.y - left_lower.y)
    right_dy = float(right_upper.y - right_lower.y)
    if abs(left_dy - right_dy) > 0.1 or not 0.0 < abs(left_dx - right_dx) <= 0.1:
        return pieces
    if float(right_upper.x) >= float(right_lower.x):
        return _move_line_node(
            pieces,
            float(right_lower.x),
            float(right_lower.y),
            float(right_upper.x) - left_dx,
        )
    return _move_line_node(
        pieces,
        float(right_upper.x),
        float(right_upper.y),
        float(right_lower.x) + left_dx,
    )
```

Call it after `_transform_groups(...)` and before `_coalesce_output_lines(...)`. Keep `validate_closed_outline`, exact target bounds, and interval metrics checks unchanged.

- [ ] **Step 3: Run slope and topology regressions**

Run:

```powershell
& $py -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_real_corpus.py -k 'professional_terminal or cross_source_collinear or curve or overlap or station' -q
```

Expected: all selected tests pass. The professional terminal directions match within `1e-6`; curve, overlap, and station rejection tests stay green.

- [ ] **Step 4: Commit the terminal-slope fix**

```powershell
git add Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py backend/tests/dxf_splitting/test_pl_real_corpus.py
git commit -m "fix: preserve PL terminal edge slopes"
```

### Task 4: Extend both professional corpora to reject future detail drift

**Files:**
- Modify: `backend/tests/dxf_splitting/test_pl_real_corpus.py`
- Modify: `backend/tests/dxf_splitting/test_pl_paired_corpus.py`

**Interfaces:**
- Consumes: saved `PLATE_CUT` entities and professional DXF boundary entities.
- Produces: corpus assertions for removable collinear nodes, terminal directions where unambiguous, target ceiling, closure, curves, label, and audit.

- [ ] **Step 1: Add a test-only removable-collinear detector**

Use endpoint equality at `0.1 mm` only to find a shared node, then use perpendicular distance to require true collinearity:

```python
def _removable_collinear_pairs(entities: tuple[DXFEntity, ...]) -> int:
    lines = tuple(entity for entity in entities if entity.dxftype() == "LINE")
    removable = 0
    for index, first in enumerate(lines):
        for second in lines[index + 1 :]:
            points = (
                (first.dxf.start, second.dxf.start),
                (first.dxf.start, second.dxf.end),
                (first.dxf.end, second.dxf.start),
                (first.dxf.end, second.dxf.end),
            )
            if any(
                hypot(float(a.x - b.x), float(a.y - b.y)) <= 0.001
                for a, b in points
            ) and _lines_share_one_straight_course(first, second):
                removable += 1
    return removable
```

Define `_lines_share_one_straight_course` in the same test file using the cross product divided by both line lengths, with `1e-6` normalized tolerance. Assert generated main boundaries contain zero removable pairs. Do not require professional files themselves to be minimally segmented.

- [ ] **Step 2: Compare unambiguous terminal pairs against professional references**

For each 21-part result, run `_terminal_directions` only when both generated and professional boundaries expose exactly two full-height LINE terminals; compare with `pytest.approx(abs=1e-6)`. For the 122-pair corpus, expand the largest professional LWPOLYLINE with `virtual_entities()`, select the largest generated `_proved_components` polygon, and apply the same comparison only when both sides expose exactly two unambiguous terminal LINEs.

- [ ] **Step 3: Run the two environment-gated corpora**

Run:

```powershell
$env:PL_PAIRED_SOURCE_DIR='E:/桌面/DWG-Agent/.tmp/pl_pairs_20260822/dxf_source'
$env:PL_PAIRED_REFERENCE_DIR='E:/桌面/DWG-Agent/.tmp/pl_pairs_20260822/dxf_result'
& $py -m pytest backend/tests/dxf_splitting/test_pl_real_corpus.py backend/tests/dxf_splitting/test_pl_paired_corpus.py -q
```

Expected: 143 professional comparisons succeed, zero PL rejects, zero removable output pairs, and no slope mismatch above `1e-6` for every unambiguous terminal pair.

- [ ] **Step 4: Commit the expanded corpus contract**

```powershell
git add backend/tests/dxf_splitting/test_pl_real_corpus.py backend/tests/dxf_splitting/test_pl_paired_corpus.py
git commit -m "test: enforce PL professional edge fidelity"
```

### Task 5: Complete regression, static review, and local handoff

**Files:**
- Verify only; no production file outside the PL scope may change.

**Interfaces:**
- Consumes: all commits from Tasks 1–4.
- Produces: a clean local branch with evidence; no remote push.

- [ ] **Step 1: Run focused PL tests**

```powershell
& $py -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_real_corpus.py backend/tests/dxf_splitting/test_pl_paired_corpus.py -q
```

Expected: all focused tests pass, including both environment-gated corpora.

- [ ] **Step 2: Run the complete DXF-splitting regression**

```powershell
& $py -m pytest backend/tests/dxf_splitting -q
```

Expected: no failures; dependency-only deprecation warnings are allowed.

- [ ] **Step 3: Run static and scope checks**

```powershell
& $py -m ruff check Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_real_corpus.py backend/tests/dxf_splitting/test_pl_paired_corpus.py
& $py -m ruff format --check Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_real_corpus.py backend/tests/dxf_splitting/test_pl_paired_corpus.py
git diff --check
rg -n 'z4-cb-17|z2-cb-79|q7-b-404|PL板材原图' Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl
```

Expected: Ruff and diff checks pass; the production-name search returns no matches.

- [ ] **Step 4: Inspect final scope and commit any formatting-only test changes**

```powershell
git status --short
git diff --stat HEAD~4..HEAD
git log --oneline -6
```

Expected: only `pl/development.py`, the three PL tests, this plan, and the approved design are present in the task history; the worktree is clean. Do not push.
