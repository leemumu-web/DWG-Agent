# PL Covered-Center Cutout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove a proven smaller circular PL cutout only when its center lies inside a proven larger circle, while preserving the larger circle and every unrelated hole or slot.

**Architecture:** Add one PL-private filter in `geometry.py` after all contained and Bolt cutout groups have been collected. The filter proves circular groups from native ARC center/radius evidence, preserves source order, and removes a group only when a strictly larger circular group covers its center by at least the existing `0.1 mm` topology tolerance.

**Tech Stack:** Python 3.12, ezdxf, Shapely, pytest, Ruff

## Global Constraints

- Apply only to PL cutout groups; do not modify BH, BOX, writer, longitudinal unfolding, or merge code.
- Remove an inner circle only when the outer radius exceeds it by at least `0.1 mm`.
- Remove it only when center distance is at most `outer radius - 0.1 mm`; concentricity and full boundary containment are not required.
- Keep circles whose centers lie outside or within `0.1 mm` of the larger boundary, plus ellipses, slots, and unproved shapes.
- Do not branch on sample filenames or part numbers in production code.
- Preserve the existing K factor, `0.1 mm` ceiling, label, outer-boundary, and saved-DXF audit contracts.

---

### Task 1: Filter small circular cutouts whose centers are covered by larger circles

**Files:**
- Modify: `backend/tests/dxf_splitting/test_pl_splitter.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/geometry.py`

**Interfaces:**
- Consumes: `tuple[tuple[DXFEntity, ...], ...]` cutout groups already proved by `analyze_geometry()`.
- Produces: `_without_large_circle_covered_centers(groups) -> tuple[tuple[DXFEntity, ...], ...]`, preserving group order and all non-matching groups.

- [ ] **Step 1: Add anonymous positive and negative fixtures**

Extend `_save_geometry_source()` with `small_circle_offset_mm: float | None = None`. When supplied, create a Part-layer radius-10 circle from two native arcs at `(200 + offset, 110)` and a Bolt-layer radius-20 circle at `(200, 110)`:

```python
if small_circle_offset_mm is not None:
    center = (200.0 + small_circle_offset_mm, 110.0)
    layout.add_arc(center, 10.0, 0.0, 180.0, dxfattribs={"layer": "Part"})
    layout.add_arc(center, 10.0, 180.0, 360.0, dxfattribs={"layer": "Part"})
    layout.add_circle((200.0, 110.0), 20.0, dxfattribs={"layer": "Bolt"})
```

Add tests that prove the exact boundary:

```python
def test_large_circle_covered_small_center_keeps_only_outer(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split.pl.geometry")
    drawing = tmp_path / "covered-center-hole.dxf"
    _save_geometry_source(drawing, small_circle_offset_mm=10.3)
    context, metadata = _load_geometry_context(drawing)

    outline, _ = geometry.analyze_geometry(context, metadata)

    assert len(outline.cutout_entity_groups) == 1
    polygon = geometry.validate_closed_outline(outline.cutout_entity_groups[0])
    assert polygon.bounds == pytest.approx((180.0, 90.0, 220.0, 130.0), abs=0.001)


def test_small_center_outside_large_circle_keeps_both(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split.pl.geometry")
    drawing = tmp_path / "offset-holes.dxf"
    _save_geometry_source(drawing, small_circle_offset_mm=20.2)
    context, metadata = _load_geometry_context(drawing)

    outline, _ = geometry.analyze_geometry(context, metadata)

    assert len(outline.cutout_entity_groups) == 2
```

- [ ] **Step 2: Run the focused tests and capture RED**

Run:

```powershell
$env:PYTHONPATH='E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\Stages\steel_dxf_split_v1.5.2\src;E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\backend'
& 'C:\Users\李某\AppData\Local\Temp\codex-pl-splitter-py312-v2\Scripts\python.exe' -m pytest backend/tests/dxf_splitting/test_pl_splitter.py -k 'covered_small_center or small_center_outside' -q
```

Expected: the `10.3 mm` covered-center positive test fails with two cutout groups; the `20.2 mm` outside-center negative test passes.

- [ ] **Step 3: Add the minimal geometry filter**

Add these helpers beside `_circle_arc_group()`:

```python
def _circle_signature(
    group: tuple[DXFEntity, ...],
) -> tuple[tuple[float, float], float] | None:
    arcs = tuple(cast(Arc, entity) for entity in group if entity.dxftype() == "ARC")
    if not arcs or len(arcs) != len(group):
        return None
    center = (float(arcs[0].dxf.center.x), float(arcs[0].dxf.center.y))
    radius = float(arcs[0].dxf.radius)
    if any(
        dist(center, (float(arc.dxf.center.x), float(arc.dxf.center.y)))
        > TOPOLOGY_TOLERANCE_MM
        or abs(float(arc.dxf.radius) - radius) > TOPOLOGY_TOLERANCE_MM
        for arc in arcs[1:]
    ):
        return None
    return center, radius


def _without_large_circle_covered_centers(
    groups: tuple[tuple[DXFEntity, ...], ...],
) -> tuple[tuple[DXFEntity, ...], ...]:
    signatures = tuple(_circle_signature(group) for group in groups)
    return tuple(
        group
        for index, (group, signature) in enumerate(zip(groups, signatures, strict=True))
        if signature is None
        or not any(
            other_index != index
            and other is not None
            and other[1] - signature[1] >= TOPOLOGY_TOLERANCE_MM
            and dist(signature[0], other[0])
            <= other[1] - TOPOLOGY_TOLERANCE_MM
            for other_index, other in enumerate(signatures)
        )
    )
```

After the existing Bolt-circle collection loop in `analyze_geometry()`, apply it once:

```python
cutout_groups = list(
    _without_large_circle_covered_centers(tuple(cutout_groups))
)
```

- [ ] **Step 4: Run focused and existing cutout tests GREEN**

Run:

```powershell
$env:PYTHONPATH='E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\Stages\steel_dxf_split_v1.5.2\src;E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\backend'
& 'C:\Users\李某\AppData\Local\Temp\codex-pl-splitter-py312-v2\Scripts\python.exe' -m pytest backend/tests/dxf_splitting/test_pl_splitter.py -k 'cutout or hole' -q
```

Expected: all selected tests pass, including the pre-existing single Bolt-hole tests.

### Task 2: Lock the four real examples and run regressions

**Files:**
- Modify: `backend/tests/dxf_splitting/test_pl_paired_corpus.py`

**Interfaces:**
- Consumes: output DXFs produced by `split_pl()` for the 122 paired source directory.
- Produces: a real-corpus assertion that each named example has one outer boundary plus exactly three retained hole components.

- [ ] **Step 1: Add the real-corpus assertion**

Add the target set at module scope:

```python
COVERED_CENTER_OUTER_ONLY_PARTS = {
    "2b1-pb-77",
    "2b1-pb-79",
    "2b1-pb-101",
    "2b1-pb-133",
}
```

Inside the existing item loop, after `_proved_components(output_cut)`:

```python
if item["part_number"] in COVERED_CENTER_OUTER_ONLY_PARTS:
    assert len(output_components) == 4
```

- [ ] **Step 2: Run the 122 paired corpus**

Run:

```powershell
$env:PYTHONPATH='E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\Stages\steel_dxf_split_v1.5.2\src;E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\backend'
$env:PL_PAIRED_SOURCE_DIR='E:\桌面\DWG-Agent\.tmp\pl_pairs_20260822\dxf_source'
$env:PL_PAIRED_REFERENCE_DIR='E:\桌面\DWG-Agent\.tmp\pl_pairs_20260822\dxf_result'
& 'C:\Users\李某\AppData\Local\Temp\codex-pl-splitter-py312-v2\Scripts\python.exe' -m pytest backend/tests/dxf_splitting/test_pl_paired_corpus.py -q
```

Expected: 122 successful splits, zero rejects, and all four named parts contain exactly three retained holes.

- [ ] **Step 3: Run PL and complete DXF regressions**

Run the 21-part real corpus, then the complete suite:

```powershell
$env:PYTHONPATH='E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\Stages\steel_dxf_split_v1.5.2\src;E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\backend'
$env:PL_REAL_SOURCE_DXF='E:\桌面\DWG-Agent\.tmp\pl_merge_dxf_20260822\merge.dxf'
$env:PL_REAL_REFERENCE_DIR='E:\桌面\DWG-Agent\.tmp\pl_dxf_batch_20260822'
& 'C:\Users\李某\AppData\Local\Temp\codex-pl-splitter-py312-v2\Scripts\python.exe' -m pytest backend/tests/dxf_splitting/test_pl_real_corpus.py -q
& 'C:\Users\李某\AppData\Local\Temp\codex-pl-splitter-py312-v2\Scripts\python.exe' -m pytest backend/tests/dxf_splitting -q
```

Expected: 21/21 real PL parts pass; the complete suite has no failures.

- [ ] **Step 4: Run static checks and sample-name guard**

Run:

```powershell
$env:PYTHONPATH='E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\Stages\steel_dxf_split_v1.5.2\src;E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\backend'
& 'C:\Users\李某\AppData\Local\Temp\codex-pl-splitter-py312-v2\Scripts\python.exe' -m ruff check Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/geometry.py backend/tests/dxf_splitting/test_pl_splitter.py backend/tests/dxf_splitting/test_pl_paired_corpus.py
& 'C:\Users\李某\AppData\Local\Temp\codex-pl-splitter-py312-v2\Scripts\python.exe' -m ruff format --check Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/geometry.py backend/tests/dxf_splitting/test_pl_splitter.py backend/tests/dxf_splitting/test_pl_paired_corpus.py
rg -n -i '2b1-pb-77|2b1-pb-79|2b1-pb-101|2b1-pb-133' Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl
git diff --check
```

Expected: Ruff and diff checks pass; production sample-name search has no matches.

- [ ] **Step 5: Publish and audit a fresh 122-part batch**

Verify `E:\桌面\PL板材\122个PL原图_大圆保留修正版_20260824` does not exist, then run the PL-only CLI without `--overwrite`:

```powershell
Test-Path -LiteralPath 'E:\桌面\PL板材\122个PL原图_大圆保留修正版_20260824'
$env:PYTHONPATH='E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\Stages\steel_dxf_split_v1.5.2\src;E:\桌面\DWG-Agent\.worktrees\codex-pl-professional-compare\backend'
& 'C:\Users\李某\AppData\Local\Temp\codex-pl-splitter-py312-v2\Scripts\python.exe' -m steel_dxf_split.pl.cli 'E:\桌面\DWG-Agent\.tmp\pl_pairs_20260822\dxf_source' --output-dir 'E:\桌面\PL板材\122个PL原图_大圆保留修正版_20260824'
```

Expected: `Test-Path` prints `False`; CLI reports 122 successes and zero rejects. Re-open every DXF with ezdxf and assert one `p=` label, zero audit errors, saved length within `0.001 mm` of `target_mm`, and four `_proved_components()` for each target example (one outer boundary plus three holes).

```powershell
@'
import json
from pathlib import Path

import ezdxf
from ezdxf import bbox
from steel_dxf_split.pl.geometry import _proved_components

output_dir = Path(r"E:/桌面/PL板材/122个PL原图_大圆保留修正版_20260824")
report = json.loads((output_dir / "pl_split_report.json").read_text(encoding="utf-8"))
targets = {"2b1-pb-77", "2b1-pb-79", "2b1-pb-101", "2b1-pb-133"}
assert report["success_count"] == 122
assert report["rejected_count"] == 0
for item in report["items"]:
    document = ezdxf.readfile(item["output"]["path"])
    plate = tuple(entity for entity in document.modelspace() if entity.dxf.layer == "PLATE_CUT")
    labels = tuple(entity for entity in document.modelspace() if entity.dxf.layer == "PART_LABEL")
    assert len(labels) == 1
    assert labels[0].dxf.text == f"p={item['part_number']}"
    assert document.audit().has_errors is False
    bounds = bbox.extents(plate, fast=False)
    assert abs(float(bounds.extmax.x - bounds.extmin.x) - item["lengths"]["target_mm"]) <= 0.001
    if item["part_number"] in targets:
        assert len(_proved_components(plate)) == 4
'@ | & 'C:\Users\李某\AppData\Local\Temp\codex-pl-splitter-py312-v2\Scripts\python.exe' -
```

- [ ] **Step 6: Commit locally without pushing**

```powershell
git add -- Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/geometry.py backend/tests/dxf_splitting/test_pl_splitter.py backend/tests/dxf_splitting/test_pl_paired_corpus.py
git diff --cached --check
git commit -m "fix: omit PL cutouts covered by larger circles"
```
