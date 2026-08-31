# PL Manufacturing Node Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the independent PL splitter emit the professional 14-segment `z2-cb-79` manufacturing topology without the compiler-created 0.501 mm point-like segment, while keeping a closed 0.1 mm manufacturing contour.

**Architecture:** Preserve short native connector evidence before affine transformation, normalize compiler-only submillimetre connectors into one protected shared node, and pass protected nodes into the existing output LINE coalescer. The rule is derived from cyclic upper/lower boundary roles and source provenance; production code never reads a part number, reference directory, or fixed sample coordinate.

**Tech Stack:** Python 3.12, ezdxf 1.4.4, Shapely, pytest, Ruff.

## Global Constraints

- Modify only `steel_dxf_split.pl` and PL tests; do not change BH, BOX, merge output, K factor, labels, or public report schema.
- Target length remains `ceil(max(projection, K length, BOM), 0.1 mm)` and never rounds down.
- Saved output must be closed within 0.1 mm, contain no POINT or zero-length entity, and pass ezdxf audit.
- `z2-cb-79` may appear only in tests and documentation, never production code.
- Do not overwrite source drawings or existing result directories and do not push remotely.

---

### Task 1: Capture the manufacturing-node regression

**Files:**
- Modify: `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`
- Modify: `backend/tests/dxf_splitting/test_pl_real_corpus.py`

**Interfaces:**
- Consumes: `transform_outline(...) -> tuple[tuple[DXFEntity, ...], DevelopmentMetrics]` and the env-gated 21-part corpus.
- Produces: anonymous connector tests plus a real-corpus assertion for 14 LINE entities and no point-like compiler connector.

- [ ] **Step 1: Add an anonymous paired short-connector fixture**

Create one closed LINE ring whose upper and lower chains both break at X=400.0/400.5, and a second ring whose upper chain alone breaks at X=700.0/700.5:

```python
paired_courses = (
    ((0.0, 100.0), (400.0, 100.0)),
    ((400.0, 100.0), (400.5, 100.0)),
    ((400.5, 100.0), (1000.0, 100.0)),
    ((1000.0, 100.0), (1000.0, 0.0)),
    ((1000.0, 0.0), (400.5, 0.0)),
    ((400.5, 0.0), (400.0, 0.0)),
    ((400.0, 0.0), (0.0, 0.0)),
    ((0.0, 0.0), (0.0, 100.0)),
)
assert len(transformed) == 7
assert sum(
    entity.dxftype() == "LINE"
    and 0.49 <= entity.dxf.start.distance(entity.dxf.end) <= 0.51
    for entity in transformed
) == 1
assert validate_closed_outline(transformed, tolerance_mm=0.1).is_valid
```

For the unpaired fixture, assert five output LINE entities, zero LINE entities of length at most 0.6 mm, and a valid closed polygon. These fixtures contain no production part number or corpus coordinate.

- [ ] **Step 2: Strengthen the real-corpus assertion**

For the generated `z2-cb-79.dxf`, assert exactly 14 `PLATE_CUT` LINE entities, no `POINT`, no zero-length entity, and exactly one LINE whose length is between 0.49 and 0.51 mm. The count plus closed-cycle order distinguishes the retained lower manufacturing connector from the removed compiler connector without putting this identity in production code.

- [ ] **Step 3: Run the focused tests and record RED**

Run:

```powershell
$env:PYTHONPATH='Stages/steel_dxf_split_v1.5.2/src'
python -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -k 'manufacturing_connector' -q
python -m pytest backend/tests/dxf_splitting/test_pl_real_corpus.py -q
```

Expected: the anonymous case and real 14-segment assertion fail against the current 13-segment output.

### Task 2: Preserve native manufacturing nodes and collapse compiler connectors

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py`
- Test: `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`

**Interfaces:**
- Consumes: `BoundaryPiece(source_index, source_handle, entity, is_noded_piece)` and the existing `_NativePiece` transform pipeline.
- Produces: `_connector_roles(boundary_pieces, min_y, max_y) -> dict[int, str]`, `_normalize_short_connectors(...) -> tuple[tuple[_NativePiece, ...], tuple[Vec3, ...]]`, and `_coalesce_output_lines(..., protected_nodes=...)`.

- [ ] **Step 1: Carry source connector provenance through `_NativePiece`**

Extend `_NativePiece` with immutable connector role values and preserve them through `_transform_groups`, `_move_line_node`, splitting, and uniform fallback construction.

```python
@dataclass(frozen=True, slots=True)
class _NativePiece:
    source_index: int
    entity: DXFEntity
    connector_role: str = "ordinary"
```

Allowed roles are `ordinary`, `preserve_lower`, and `collapse`; no sample identity is stored.

- [ ] **Step 2: Classify submillimetre source connectors by cyclic side evidence**

From `canonical_boundary_pieces(source)`, collect LINE pieces of length at most 0.6 mm. Pair upper and lower candidates only when their X ranges overlap within 0.6 mm. Mark the lower member of a proven pair `preserve_lower`; mark its upper member and all unpaired candidates `collapse`. If side or pairing is ambiguous, leave the piece `ordinary` rather than guessing.

```python
def _connector_roles(
    boundary_pieces: tuple[BoundaryPiece, ...],
    *,
    min_y: float,
    max_y: float,
) -> dict[int, str]:
    candidates = tuple(
        piece
        for piece in boundary_pieces
        if piece.entity.dxftype() == "LINE"
        and piece.entity.dxf.start.distance(piece.entity.dxf.end) <= 0.6
    )
    roles: dict[int, str] = {}
    middle_y = (min_y + max_y) / 2.0
    for piece in candidates:
        midpoint_y = (piece.entity.dxf.start.y + piece.entity.dxf.end.y) / 2.0
        side = "lower" if midpoint_y < middle_y else "upper"
        opposites = tuple(
            other
            for other in candidates
            if ((other.entity.dxf.start.y + other.entity.dxf.end.y) / 2.0 < middle_y)
            != (side == "lower")
            and max(
                min(piece.entity.dxf.start.x, piece.entity.dxf.end.x),
                min(other.entity.dxf.start.x, other.entity.dxf.end.x),
            )
            <= min(
                max(piece.entity.dxf.start.x, piece.entity.dxf.end.x),
                max(other.entity.dxf.start.x, other.entity.dxf.end.x),
            )
            + 0.6
        )
        if len(opposites) == 1:
            roles[piece.source_index] = (
                "preserve_lower" if side == "lower" else "collapse"
            )
        elif not opposites:
            roles[piece.source_index] = "collapse"
    return roles
```

- [ ] **Step 3: Normalize collapsible connectors**

For each `collapse` LINE, require exactly two adjacent LINE neighbours, no curve or branch, and a union direction change within the existing 0.1 mm detail tolerance. Replace its two endpoints by their midpoint on the neighbours, remove the connector, and return the midpoint as a protected node. For `preserve_lower`, return both endpoints as protected nodes and retain the entity. Ambiguous adjacency fails closed with `PLSplitError("TRANSFORM_CONNECTOR", ...)`.

- [ ] **Step 4: Protect manufacturing nodes during ordinary coalescing**

Change the existing helper to:

```python
def _coalesce_output_lines(
    pieces: tuple[_NativePiece, ...],
    *,
    protected_nodes: tuple[Vec3, ...] = (),
) -> tuple[_NativePiece, ...]:
```

Skip a merge when its shared endpoint is within 0.1 mm of a protected node. Keep the existing no-overlap direction guard and curve/branch safety rules.

- [ ] **Step 5: Run focused GREEN tests**

Run the anonymous manufacturing-connector tests together with cross-source coalescing, contained-overlap, native-curve, saved-output, and slope-preservation tests. Expected: all selected tests pass.

- [ ] **Step 6: Commit the production fix**

```powershell
git add Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_real_corpus.py
git commit -m "fix: preserve PL manufacturing nodes"
```

### Task 3: Rebuild and validate the professional batch

**Files:**
- Modify: `backend/tests/dxf_splitting/test_pl_real_corpus.py`
- Output: a new non-overwriting corrected result directory beside the prior 21-part output.

**Interfaces:**
- Consumes: the merged source DXF and the independent PL CLI.
- Produces: 21 DXFs plus schema-2 JSON report.

- [ ] **Step 1: Run core and corpus regressions**

Run 59+ PL carrier tests, the env-gated 21-part corpus, the env-gated 122-pair corpus, and all `backend/tests/dxf_splitting` tests. Expected: zero failures; corpus counts 21/21 and 122/122.

- [ ] **Step 2: Run static checks**

Run `py_compile`, `ruff check`, `git diff --check`, and a production-source search for all known sample names. Expected: clean checks and no production sample-name matches.

- [ ] **Step 3: Publish one fresh corrected batch**

Run the independent PL CLI against `E:/桌面/DWG-Agent/.tmp/pl_merge_dxf_20260822/merge.dxf` into a new absent directory. Do not use `--overwrite`. Validate 21 DXFs, zero rejects, zero audit errors, all labels prefixed `p=`, and `z2-cb-79` exactly 14 LINE entities with no POINT or point-like compiler segment.

- [ ] **Step 4: Commit remaining test/report changes locally**

```powershell
git add backend/tests/dxf_splitting/test_pl_real_corpus.py
git commit -m "test: lock PL professional node topology"
```

Do not push the branch.
