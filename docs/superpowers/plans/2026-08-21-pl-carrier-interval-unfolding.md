# PL Carrier-Interval Unfolding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the standalone PL splitter's global X scaling with one generic carrier-interval algorithm that measures every turn, applies one strictly ceiled total extension to the uniquely inferred carrier interval, preserves every other interval, and writes a 30 mm `p=` label.

**Architecture:** Keep the existing independent `steel_dxf_split.pl` package and launcher. Add a focused longitudinal-topology module that derives ordered station bands and one carrier zone from each part's own outline; keep whole-section K=0.5 length authority in `geometry.py`, total-length arithmetic and native entity transformation in `development.py`, and saved-output/report validation in the existing writer/compiler boundary. No part number, sample coordinate, BH/BOX, or merge code participates in geometric decisions.

**Tech Stack:** Python 3.12, ezdxf 1.4.4, Shapely 2.1.x, pytest 8/9, PowerShell on Windows.

**Spec:** `docs/superpowers/specs/2026-08-21-pl-bent-plate-splitting-design.md`

## Global Constraints

- K factor is exactly `0.5`; the whole-section authority remains `L_K = A_section / thickness`.
- Compute `L_raw = max(L_projection, L_K, L_bom)` and apply decimal `ROUND_CEILING` to `0.1 mm` exactly once.
- Do not suppress or round down any positive residual: `470.0 -> 470.0`, `470.0000001 -> 470.1`.
- Compute `E_total = L_target - L_projection` and apply all of it to one uniquely inferred carrier zone.
- Measure every ordered station interval; upstream geometry stays fixed, the carrier grows by `E_total`, and downstream geometry translates by `E_total` without scaling.
- Infer the carrier only from outline topology and dimensions. Production code must not branch on part number, filename, block name, handle, sample coordinate, or reference-result delta.
- A paired visible turn requires both longitudinal boundaries to change Y by more than `0.001 mm`; with no visible turn, use the unique longest body interval, treating a longest/second-longest difference of at most `0.1 mm` as ambiguous.
- A station band's X width must not exceed nominal thickness plus `0.1 mm`.
- Ambiguous or disjoint carrier candidates fail closed; there is no fallback to global scaling.
- Output remains R2007, millimetres, 1:1, with cut geometry on `PLATE_CUT`, one `p=<part-number>` mark on `PART_LABEL`, and a fixed text height of `30 mm`.
- A locally X-scaled `ARC` is emitted as an exact `ELLIPSE`; unchanged curves retain their native type.
- Do not modify BH/BOX runtime behavior, the protected `steel-dxf-split` entrypoint, backend routing, or `tools/merge_sheet.py`.
- Do not add merging/composite-image behavior or direct DWG read/write to the PL module.
- User sample drawings are read-only, untracked acceptance data. Write regenerated results to a new directory and never overwrite the earlier result directory.
- Commit locally only. Do not push, merge to the main branch, create a PR, or publish a remote branch.

## Local Environment and Baseline

Use the existing disposable Python 3.12 environment:

```powershell
$env:PL_TEST_PYTHON = Join-Path $env:TEMP 'codex-pl-splitter-py312\Scripts\python.exe'
& $env:PL_TEST_PYTHON --version
& $env:PL_TEST_PYTHON -c "import ezdxf, shapely; print(ezdxf.__version__, shapely.__version__)"
```

Expected: Python 3.12.x, ezdxf 1.4.4, and Shapely 2.1.x.

Before the first RED test, confirm the recorded baseline:

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py -q
```

Expected: `35 passed`.

## File Map

- Modify `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py`: replace global-scale evidence with immutable station, interval, carrier-zone, and per-interval development evidence.
- Create `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py`: order native outer-boundary entities, build paired longitudinal chains and station bands, reject ambiguity, and select one carrier zone without metadata shortcuts.
- Modify `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py`: enforce strict one-time total rounding and perform upstream/carrier/downstream native transformations.
- Modify `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/compiler.py`: pass longitudinal proof through compilation and publish report schema 2 interval evidence.
- Modify `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/writer.py`: use a fixed 30 mm mark, allow station splitting to change entity count, and validate saved interval geometry.
- Modify `backend/tests/dxf_splitting/test_pl_splitter.py`: update the existing PL integration fixtures and assertions to the new public contracts.
- Create `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`: focused arithmetic, carrier inference, local transform, and ambiguity tests.
- Create `backend/tests/dxf_splitting/test_pl_real_corpus.py`: environment-gated 21-part class-generalization acceptance without committing user drawings.
- Modify `D:/下载/PL折弯板拆板规则总结.md`: align the user-facing rules with one-time total rounding, carrier inference, and 30 mm marks.

---

### Task 1: Freeze strict total-length arithmetic and evidence contracts

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py`
- Create: `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`
- Modify: `backend/tests/dxf_splitting/test_pl_splitter.py`

**Interfaces:**
- Produces `DevelopmentTarget(projection_length_mm, k_length_mm, bom_length_mm, raw_length_mm, target_length_mm, total_extension_mm)`.
- Produces `calculate_target(*, projection_length_mm: float, k_length_mm: float, bom_length_mm: float) -> DevelopmentTarget`.
- Keeps `ceil_tenth_mm(value_mm: float | Decimal) -> Decimal`, but removes all downward noise snapping.
- Declares shared contracts used by later tasks: `StationBand`, `LongitudinalIntervalEvidence`, `LongitudinalProof`, and `DevelopedIntervalMetrics`.

- [ ] **Step 1: Write strict-ceiling and q7 total-target tests**

Add these tests to `test_pl_carrier_unfolding.py`:

```python
from decimal import Decimal

import pytest

from steel_dxf_split.pl.development import calculate_target, ceil_tenth_mm


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Decimal("470.0"), Decimal("470.0")),
        (Decimal("470.0000001"), Decimal("470.1")),
        (Decimal("470.0999999"), Decimal("470.1")),
        (Decimal("470.1000001"), Decimal("470.2")),
    ],
)
def test_strict_tenth_ceiling_never_absorbs_a_positive_residual(
    source: Decimal,
    expected: Decimal,
) -> None:
    assert ceil_tenth_mm(source) == expected


def test_q7_b_404_uses_one_total_ceiling_without_per_interval_growth() -> None:
    target = calculate_target(
        projection_length_mm=1154.065614079,
        k_length_mm=1162.124078060,
        bom_length_mm=1162.0,
    )

    assert target.raw_length_mm == pytest.approx(1162.124078060)
    assert target.target_length_mm == pytest.approx(1162.2)
    assert target.total_extension_mm == pytest.approx(8.134385921)
    assert target.total_extension_mm < 8.2
```

Update the old parametrized rounding test in `test_pl_splitter.py`: replace the `10.0000004 -> 10.0` expectation with `10.0000004 -> 10.1`. Do not retain a floating-noise exception.

- [ ] **Step 2: Run the focused RED tests**

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_splitter.py -k "strict_tenth or q7_b_404 or length_is_ceiled" -q
```

Expected: FAIL because `calculate_target` and `DevelopmentTarget` do not exist and the current noise snap returns the lower tenth.

- [ ] **Step 3: Add final immutable evidence types**

Define these exact contracts in `contracts.py`:

```python
@dataclass(frozen=True, slots=True)
class DevelopmentTarget:
    projection_length_mm: float
    k_length_mm: float
    bom_length_mm: float
    raw_length_mm: float
    target_length_mm: float
    total_extension_mm: float


@dataclass(frozen=True, slots=True)
class StationBand:
    index: int
    upper_x_mm: float
    lower_x_mm: float
    source_entity_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LongitudinalIntervalEvidence:
    index: int
    left_station: StationBand
    right_station: StationBand
    upper_entity_indices: tuple[int, ...]
    lower_entity_indices: tuple[int, ...]
    upper_span_mm: float
    lower_span_mm: float
    upper_delta_y_mm: float
    lower_delta_y_mm: float
    is_end_feature: bool
    is_turn_candidate: bool
    source_handles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LongitudinalProof:
    intervals: tuple[LongitudinalIntervalEvidence, ...]
    carrier_interval_indices: tuple[int, ...]
    selection_reason: str


@dataclass(frozen=True, slots=True)
class DevelopedIntervalMetrics:
    index: int
    source_upper_span_mm: float
    source_lower_span_mm: float
    output_upper_span_mm: float
    output_lower_span_mm: float
    downstream_shift_mm: float
    is_carrier: bool
```

Keep the existing `DevelopmentMetrics` and `DevelopedPlate` fields unchanged in this task so the global-transform baseline remains runnable. Task 3 replaces those two contracts and all call sites atomically in one test cycle.

- [ ] **Step 4: Implement strict target calculation**

Replace the noise-snap implementation with:

```python
def ceil_tenth_mm(value_mm: float | Decimal) -> Decimal:
    value = value_mm if isinstance(value_mm, Decimal) else Decimal(str(value_mm))
    if not value.is_finite() or value <= 0:
        raise PLSplitError("INVALID_LENGTH", "展开长度必须是正的有限毫米值。")
    return value.quantize(Decimal("0.1"), rounding=ROUND_CEILING)


def calculate_target(
    *,
    projection_length_mm: float,
    k_length_mm: float,
    bom_length_mm: float,
) -> DevelopmentTarget:
    projection = _positive_finite(projection_length_mm, "主视图投影长度")
    k_length = _positive_finite(k_length_mm, "K=0.5中性层长度")
    bom = _positive_finite(bom_length_mm, "材料表长度")
    raw = max(projection, k_length, bom)
    target = float(ceil_tenth_mm(raw))
    return DevelopmentTarget(
        projection_length_mm=projection,
        k_length_mm=k_length,
        bom_length_mm=bom,
        raw_length_mm=raw,
        target_length_mm=target,
        total_extension_mm=target - projection,
    )
```

Do not add an epsilon subtraction anywhere in the rounding path.

- [ ] **Step 5: Run arithmetic tests and commit**

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_splitter.py -k "strict_tenth or q7_b_404 or length_is_ceiled or k_half" -q
git add -- backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_splitter.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py
git commit -m "fix: enforce one strict PL length ceiling"
```

Expected: selected tests pass; commit remains local.

---

### Task 2: Infer one carrier zone from generic longitudinal features

**Files:**
- Create: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py`
- Modify: `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`

**Interfaces:**
- Consumes `PlateOutline.outer_entities`, `PlateOutline.polygon`, and nominal thickness; it never consumes `PLMetadata.part_number`.
- Produces `analyze_longitudinal_outline(entities: tuple[DXFEntity, ...], polygon: Polygon, *, thickness_mm: float) -> LongitudinalProof`.
- Produces `select_carrier_zone(intervals: tuple[LongitudinalIntervalEvidence, ...]) -> tuple[tuple[int, ...], str]`.
- Private helpers order the closed entity ring, split it into two paired longitudinal chains plus end chains, build station bands, merge adjacent turn fragments, and reject ambiguous topology.

- [ ] **Step 1: Add carrier-selection RED tests**

Add a local interval builder and these cases to `test_pl_carrier_unfolding.py`:

```python
from steel_dxf_split.pl.contracts import LongitudinalIntervalEvidence, StationBand
from steel_dxf_split.pl.longitudinal import select_carrier_zone


def _interval(
    index: int,
    start: float,
    end: float,
    *,
    upper_dy: float = 0.0,
    lower_dy: float = 0.0,
    end_feature: bool = False,
) -> LongitudinalIntervalEvidence:
    return LongitudinalIntervalEvidence(
        index=index,
        left_station=StationBand(index, start, start, (index,)),
        right_station=StationBand(index + 1, end, end, (index + 1,)),
        upper_entity_indices=(index * 2,),
        lower_entity_indices=(index * 2 + 1,),
        upper_span_mm=end - start,
        lower_span_mm=end - start,
        upper_delta_y_mm=upper_dy,
        lower_delta_y_mm=lower_dy,
        is_end_feature=end_feature,
        is_turn_candidate=abs(upper_dy) > 0.001 and abs(lower_dy) > 0.001,
        source_handles=(f"u{index}", f"l{index}"),
    )


def test_visible_middle_turn_wins_without_using_part_identity() -> None:
    intervals = (
        _interval(0, 0.0, 554.06564),
        _interval(1, 554.06564, 851.99350, upper_dy=-50.0, lower_dy=50.0),
        _interval(2, 851.99350, 1154.065614),
    )

    assert select_carrier_zone(intervals) == ((1,), "paired_visible_turn")


def test_flat_outline_uses_unique_longest_body_interval() -> None:
    intervals = (
        _interval(0, 0.0, 1176.513),
        _interval(1, 1176.513, 1200.974, end_feature=True),
    )

    assert select_carrier_zone(intervals) == ((0,), "unique_longest_body")


def test_disjoint_turn_candidates_fail_closed() -> None:
    intervals = (
        _interval(0, 0.0, 200.0, upper_dy=20.0, lower_dy=-20.0),
        _interval(1, 200.0, 400.0),
        _interval(2, 400.0, 600.0, upper_dy=-20.0, lower_dy=20.0),
    )

    with pytest.raises(PLSplitError) as error:
        select_carrier_zone(intervals)

    assert error.value.code == "CARRIER_AMBIGUOUS"
```

Add cases for adjacent turn fragments merging into one zone, a longest-interval tie within `0.1 mm`, and a station band wider than `thickness + 0.1 mm`.

- [ ] **Step 2: Run selection tests and verify the missing module**

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -k "carrier or turn or longest or station_band" -q
```

Expected: collection fails because `steel_dxf_split.pl.longitudinal` does not exist.

- [ ] **Step 3: Implement deterministic carrier selection**

Use this exact selection core after topology extraction has populated interval evidence:

```python
def _consecutive_groups(indices: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    groups: list[list[int]] = []
    for index in indices:
        if not groups or index != groups[-1][-1] + 1:
            groups.append([index])
        else:
            groups[-1].append(index)
    return tuple(tuple(group) for group in groups)


def select_carrier_zone(
    intervals: tuple[LongitudinalIntervalEvidence, ...],
) -> tuple[tuple[int, ...], str]:
    body = tuple(interval for interval in intervals if not interval.is_end_feature)
    if not body:
        raise PLSplitError("CARRIER_MISSING", "主视图没有可用纵向主体区间。")
    turns = tuple(interval.index for interval in body if interval.is_turn_candidate)
    if turns:
        groups = _consecutive_groups(turns)
        if len(groups) != 1:
            raise PLSplitError("CARRIER_AMBIGUOUS", "主视图存在多个不相邻转折承载候选。")
        return groups[0], "paired_visible_turn"
    ranked = sorted(
        body,
        key=lambda interval: (-min(interval.upper_span_mm, interval.lower_span_mm), interval.index),
    )
    if len(ranked) > 1:
        first = min(ranked[0].upper_span_mm, ranked[0].lower_span_mm)
        second = min(ranked[1].upper_span_mm, ranked[1].lower_span_mm)
        if first - second <= 0.1:
            raise PLSplitError("CARRIER_AMBIGUOUS", "主视图最长主体区间不唯一。")
    return (ranked[0].index,), "unique_longest_body"
```

- [ ] **Step 4: Add generated-outline integration tests**

Create generated native `LINE` outlines for four profiles: middle visible turn, first visible turn, last visible turn, and flat body plus a short end tab. For each, call `analyze_longitudinal_outline()` and assert the carrier position. Re-run the same middle-turn geometry with two unrelated part-number strings only at the test layer and assert identical `LongitudinalProof`; the production function receives neither string.

```python
proof = analyze_longitudinal_outline(
    entities,
    polygon,
    thickness_mm=30.0,
)
assert proof.carrier_interval_indices == (1,)
assert proof.selection_reason == "paired_visible_turn"
assert tuple(interval.index for interval in proof.intervals) == (0, 1, 2)
```

- [ ] **Step 5: Run the topology integration tests in RED**

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -k "longitudinal" -q
```

Expected: FAIL because `analyze_longitudinal_outline()` has not built boundary chains or station bands yet.

- [ ] **Step 6: Implement outline topology extraction**

In `analyze_longitudinal_outline()`:

1. Clone and flatten each proven outer entity with `flatten_entity(entity, 0.001)`.
2. Reuse the existing `0.1 mm` endpoint node tolerance to build a degree-two closed ring; reject branches or disconnected pieces with `LONGITUDINAL_TOPOLOGY`.
3. Traverse the ring deterministically from the lowest `(x, y, entity_index)` node.
4. Form opposing longitudinal candidates only when their X projections overlap and they belong to opposite sides of the polygon; end-cap chains and unmatched small ledges are marked `is_end_feature=True`.
5. Build station bands from paired path endpoints and full-width internal projection evidence. Reject any band whose `abs(upper_x_mm - lower_x_mm) > thickness_mm + 0.1` with `STATION_BAND_TOO_WIDE`.
6. Set `is_turn_candidate` only when both `abs(upper_delta_y_mm)` and `abs(lower_delta_y_mm)` are greater than `0.001`.
7. Call `select_carrier_zone()` and return immutable evidence with source entity indices and handles.

Keep all geometry decisions independent of text and metadata except the numeric thickness limit.

- [ ] **Step 7: Run topology tests and commit**

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -k "carrier or turn or longest or station_band or longitudinal" -q
git add -- backend/tests/dxf_splitting/test_pl_carrier_unfolding.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py
git commit -m "feat: infer generic PL carrier intervals"
```

Expected: all selected tests pass and no sample identifiers appear under `steel_dxf_split/pl/`.

---

### Task 3: Apply the total extension only to the carrier zone

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/compiler.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py`
- Modify: `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`
- Modify: `backend/tests/dxf_splitting/test_pl_splitter.py`

**Interfaces:**
- Consumes `LongitudinalProof`, whole-section `k_length_mm`, BOM length, and proven native outer entities.
- Replaces the old global-scale signature with `transform_outline(entities, *, longitudinal, projection_length_mm, k_length_mm, bom_length_mm, anchor_x_mm) -> tuple[tuple[DXFEntity, ...], DevelopmentMetrics]`.
- Produces transformed native entities, per-interval before/after evidence, two carrier-chain scale factors, and one downstream shift.
- `compile_context()` calls `analyze_geometry()`, then `analyze_longitudinal_outline()`, then `transform_outline()`.

- [ ] **Step 1: Write the q7 piecewise-transform RED test**

Use a generated q7-like three-course outline whose stations are `0`, `554.065640`, `851.993500`, and `1154.065614`. Assert:

```python
transformed, metrics = transform_outline(
    entities,
    longitudinal=proof,
    projection_length_mm=1154.065614,
    k_length_mm=1162.124078,
    bom_length_mm=1162.0,
    anchor_x_mm=0.0,
)

assert metrics.target_length_mm == pytest.approx(1162.2)
assert metrics.total_extension_mm == pytest.approx(8.134386, abs=1e-6)
assert metrics.carrier_interval_indices == (1,)
assert metrics.intervals[0].output_upper_span_mm == pytest.approx(
    metrics.intervals[0].source_upper_span_mm
)
assert metrics.intervals[1].output_upper_span_mm == pytest.approx(
    metrics.intervals[1].source_upper_span_mm + metrics.total_extension_mm
)
assert metrics.intervals[2].output_upper_span_mm == pytest.approx(
    metrics.intervals[2].source_upper_span_mm
)
assert metrics.intervals[2].downstream_shift_mm == pytest.approx(8.134386, abs=1e-6)
```

Also assert the normalized result stations are `0`, `554.065640`, `860.127886`, and `1162.2` within `0.001 mm`.

- [ ] **Step 2: Add curve and station-band RED tests**

Add a carrier-zone fixture containing an `ARC`, plus a right station band whose upper and lower X endpoints differ by less than thickness. Assert the carrier arc becomes `ELLIPSE`, both right-station endpoints move by exactly `E_total`, and a downstream line keeps its original X span and Y coordinates.

```python
assert any(entity.dxftype() == "ELLIPSE" for entity in transformed)
assert right_upper_x_out - right_upper_x_in == pytest.approx(metrics.total_extension_mm)
assert right_lower_x_out - right_lower_x_in == pytest.approx(metrics.total_extension_mm)
assert downstream_span_out == pytest.approx(downstream_span_in)
assert downstream_y_out == pytest.approx(downstream_y_in)
```

- [ ] **Step 3: Run transform tests and verify the old global behavior fails**

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -k "piecewise or transform or station_band or curve" -q
```

Expected: FAIL because the current `transform_outline()` accepts surface lengths and applies one global `scale_x`.

- [ ] **Step 4: Replace development result contracts atomically**

Replace `DevelopmentMetrics` with:

```python
@dataclass(frozen=True, slots=True)
class DevelopmentMetrics:
    projection_length_mm: float
    k_factor: float
    k_length_mm: float
    bom_length_mm: float
    raw_length_mm: float
    target_length_mm: float
    total_extension_mm: float
    anchor_x_mm: float
    carrier_interval_indices: tuple[int, ...]
    carrier_upper_scale_x: float
    carrier_lower_scale_x: float
    intervals: tuple[DevelopedIntervalMetrics, ...]
```

Add `longitudinal: LongitudinalProof` to `DevelopedPlate`. Remove `surface_lengths_mm` and global `scale_x` in the same edit that updates `development.py`, `compiler.py`, and existing test fixtures, so no commit contains a half-migrated contract.

- [ ] **Step 5: Implement upstream/carrier/downstream transforms**

Compute the target once with `calculate_target()`. Derive the carrier's upper and lower X spans from the first and last carrier intervals. For each chain use:

```python
upper_scale = (upper_span + target.total_extension_mm) / upper_span
lower_scale = (lower_span + target.total_extension_mm) / lower_span
```

Classify ordered native pieces into:

- upstream and left-station pieces: identity copy;
- carrier upper/lower pieces: chain-specific X scale anchored at that chain's left station;
- right-station and downstream pieces: `Matrix44.translate(target.total_extension_mm, 0.0, 0.0)`.

Split any native entity that crosses a station boundary before transforming it. For `LINE`, split at the exact interpolation parameter. For `ARC`, solve the vertical-station intersections in the arc's CCW angle range and clone angle subranges. For `ELLIPSE`, solve the construction ellipse's X parameter intersections and clone parameter subranges. Reject failed or ambiguous splits with `STATION_SPLIT_FAILED`; never flatten a manufacturing curve into line segments.

Use `ezdxf.transform.copies()` for each affine group. Reject transform log entries. Accept a larger output entity count because exact station splitting is allowed.

- [ ] **Step 6: Build final interval metrics and validate geometry in memory**

Measure `source_width_mm` from the native outline bounding box before any transform. After transforming, call `validate_closed_outline()` and measure the output bounds. Require:

```python
abs(output_length - target.target_length_mm) <= 0.001
abs(output_width - source_width_mm) <= 0.001
abs(output_min_x - anchor_x_mm) <= 0.001
```

Build one `DevelopedIntervalMetrics` per source interval. Carrier-zone output spans must equal source spans plus the proportional share created by the one affine carrier-zone scale; non-carrier spans must equal source spans within `0.001 mm`, and every downstream interval records `target.total_extension_mm` as its shift.

- [ ] **Step 7: Update compilation data flow**

Change `compile_context()` to:

```python
outline, section = analyze_geometry(context, metadata)
longitudinal = analyze_longitudinal_outline(
    outline.outer_entities,
    outline.polygon,
    thickness_mm=metadata.thickness_mm,
)
transformed, metrics = transform_outline(
    outline.outer_entities,
    longitudinal=longitudinal,
    projection_length_mm=outline.projection_length_mm,
    k_length_mm=section.k_length_mm,
    bom_length_mm=metadata.bom_length_mm,
    anchor_x_mm=outline.anchor_x_mm,
)
developed = DevelopedPlate(
    metadata=metadata,
    outline=outline,
    section=section,
    longitudinal=longitudinal,
    transformed_entities=transformed,
    metrics=metrics,
)
```

Remove every remaining runtime use of `section.equivalent_surface_lengths_mm` and global `metrics.scale_x`.

- [ ] **Step 8: Run focused plus existing PL tests and commit**

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_splitter.py -q
git add -- backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_splitter.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/compiler.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py
git commit -m "feat: unfold PL at the inferred carrier interval"
```

Expected: all PL unit/integration tests pass; no global-scale assertion remains.

---

### Task 4: Enforce 30 mm marks and auditable saved interval output

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/writer.py`
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/compiler.py`
- Modify: `backend/tests/dxf_splitting/test_pl_splitter.py`
- Modify: `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`

**Interfaces:**
- `write_pl_dxf()` always requests exactly `30.0 mm` label height and rejects a placement that cannot retain it.
- `validate_saved_pl_dxf()` validates geometry rather than requiring output entity count to equal source entity count.
- Report schema becomes `steel-dxf-split-pl-report/2` and records the carrier decision plus every interval's before/after evidence.

- [ ] **Step 1: Write label-height and split-entity RED tests**

Extend the saved-output test:

```python
labels = list(saved.modelspace().query('TEXT[layer=="PART_LABEL"]'))
assert len(labels) == 1
assert labels[0].dxf.text == "p=q7-b-404"
assert labels[0].dxf.height == pytest.approx(30.0)
```

Add an outline that is split at one carrier boundary and assert save/reload succeeds even though `len(saved_plate_entities) > len(source_outer_entities)`. Keep the closure, width, target length, left anchor, allowed entity types, and ezdxf audit assertions.

- [ ] **Step 2: Run writer tests and verify current behavior fails**

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -k "writer or label or saved or report" -q
```

Expected: FAIL because current PL labels are width-derived and saved validation requires the old entity count.

- [ ] **Step 3: Make the PL-only label height fixed**

In `writer.py`, do not change the shared BH/BOX helper. Replace the PL preferred-height expression with:

```python
PL_LABEL_HEIGHT_MM = 30.0

placement = layout_part_marks(
    (
        PartMarkTarget(
            target_id=developed.metadata.part_number,
            label=label,
            outer_geometry=developed_polygon,
            material_geometry=developed_polygon,
        ),
    ),
    preferred_height_mm=PL_LABEL_HEIGHT_MM,
)[0]
if abs(placement.height_mm - PL_LABEL_HEIGHT_MM) > 1e-9:
    raise PLSplitError("PL_LABEL_DOES_NOT_FIT", "30 mm零件标记无法完整放入板材区域。")
```

Validate the reopened `TEXT.dxf.height` equals `30.0` within `1e-9`.

- [ ] **Step 4: Replace entity-count validation with interval validation**

Allow only `LINE`, `ARC`, and `ELLIPSE` on `PLATE_CUT`, but do not compare count with source entities. Rebuild the saved polygon, check target length/width/anchor, then compare saved station and interval measurements with `developed.metrics.intervals` within `0.001 mm`. Require exactly one carrier zone and unchanged non-carrier spans.

- [ ] **Step 5: Publish report schema 2**

Set:

```python
REPORT_SCHEMA = "steel-dxf-split-pl-report/2"
```

Replace the old `transform.scale_x` payload with:

```python
"transform": {
    "carrier_interval_indices": list(metrics.carrier_interval_indices),
    "selection_reason": developed.longitudinal.selection_reason,
    "total_extension_mm": metrics.total_extension_mm,
    "carrier_upper_scale_x": metrics.carrier_upper_scale_x,
    "carrier_lower_scale_x": metrics.carrier_lower_scale_x,
    "intervals": [
        {
            "index": interval.index,
            "source_upper_span_mm": interval.source_upper_span_mm,
            "source_lower_span_mm": interval.source_lower_span_mm,
            "output_upper_span_mm": interval.output_upper_span_mm,
            "output_lower_span_mm": interval.output_lower_span_mm,
            "downstream_shift_mm": interval.downstream_shift_mm,
            "is_carrier": interval.is_carrier,
        }
        for interval in metrics.intervals
    ],
},
```

Add `total_extension_mm` under `lengths`. Update the existing batch-report test to expect schema 2, exactly one carrier zone, and no `scale_x` key.

- [ ] **Step 6: Run all PL tests and commit**

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -q
git add -- backend/tests/dxf_splitting/test_pl_splitter.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/writer.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/compiler.py
git commit -m "fix: validate PL carrier output and 30 mm labels"
```

Expected: all PL tests pass, including save/reload and launcher isolation.

---

### Task 5: Prove class generalization on all 21 parts and align rules

**Files:**
- Create: `backend/tests/dxf_splitting/test_pl_real_corpus.py`
- Modify: `D:/下载/PL折弯板拆板规则总结.md`
- Verify only: user source/reference DXFs and a fresh local result directory

**Interfaces:**
- The real-corpus test is skipped unless `PL_REAL_SOURCE_DXF` and `PL_REAL_REFERENCE_DIR` are set.
- Test-only sample expectations may name parts; no production file under `steel_dxf_split/pl/` may contain these names.
- Produces one 21-row audit through report schema 2 and a fresh directory of regenerated DXFs.

- [ ] **Step 1: Add environment-gated corpus acceptance**

Create the test with this complete reference-position map, derived from the provided result set and used only for offline acceptance:

```python
EXPECTED_CARRIER_POSITION = {
    "q4-b-181": "middle",
    "q6-b-62": "only",
    "q6-b-71": "last",
    "q6-cb-21": "only",
    "q7-b-21": "middle",
    "q7-b-404": "middle",
    "q7-b-446": "only",
    "q7-b-623": "middle",
    "q7-b-628": "middle",
    "z2-cb-104": "last",
    "z2-cb-207": "last",
    "z2-cb-209": "first",
    "z2-cb-230": "first",
    "z2-cb-231": "only",
    "z2-cb-338": "only",
    "z2-cb-347": "first",
    "z2-cb-348": "first",
    "z2-cb-350": "first",
    "z2-cb-78": "last",
    "z2-cb-79": "last",
    "z4-cb-17": "first",
}


def _carrier_position(interval_count: int, indices: tuple[int, ...]) -> str:
    if interval_count == 1:
        return "only"
    if min(indices) == 0:
        return "first"
    if max(indices) == interval_count - 1:
        return "last"
    return "middle"
```

The test must:

1. Skip with a clear message if either environment variable is missing.
2. Assert all 21 named reference DXFs exist.
3. Run `split_pl(source_dxf, tmp_path / "results")`.
4. Assert `success_count == 21`, `rejected_count == 0`, and exact output names.
5. For every report item, assert one carrier zone, the expected test-only relative position, `target >= projection`, `target >= k`, `target >= bom`, and `0 <= target - max(projection, k, bom) < 0.1`.
6. Assert only carrier intervals grow, other spans remain within `0.001 mm`, the saved label is `p=<part>` at `30 mm`, width is unchanged, and ezdxf audit has no errors.
7. Assert `q7-b-404` reports target `1162.2` and total extension approximately `8.134386`, not `10.2`.

- [ ] **Step 2: Run the corpus test in RED against current behavior**

```powershell
$env:PL_REAL_SOURCE_DXF = 'C:\Users\李某\AppData\Local\Temp\codex-pl-review-a33244fe47b24c6b9cde9747257bc0d7\dxf\combined.dxf'
$env:PL_REAL_REFERENCE_DIR = 'C:\Users\李某\AppData\Local\Temp\codex-pl-review-a33244fe47b24c6b9cde9747257bc0d7\dxf'
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_real_corpus.py -q
```

Expected before the final topology adjustments: at least one failure identifies a concrete part, carrier inference, interval preservation, label height, or target measurement. Fix only generic topology/transform logic; do not add a production sample-name branch.

- [ ] **Step 3: Generalize from every failing shape feature**

For each failing real part:

1. Record its ordered interval evidence from report schema 2.
2. Identify the topology feature that the generic analyzer missed: paired turn fragments, end-chain ledge, station-band offset, or flat longest-body fallback.
3. Add a minimal programmatic fixture for that feature to `test_pl_carrier_unfolding.py` without using the real part number.
4. Observe the new fixture fail.
5. Change only `longitudinal.py` or generic transformation code.
6. Run the programmatic fixture, the full focused PL tests, and the real-corpus test again.

The acceptable completion result is `21 passed as one class`; reducing the expected set, skipping a named part, or branching on a name is prohibited.

- [ ] **Step 4: Update the Chinese rule summary**

Use `apply_patch` to change `D:/下载/PL折弯板拆板规则总结.md` so it explicitly states:

- K factor is fixed at 0.5 and proven by whole-section area divided by thickness.
- `max(投影总长, K展开总长, 材料表长度)` is ceiled once to one decimal; positive residuals never round down.
- Every turn is measured, but per-turn differences are not separately ceiled or summed.
- One generic carrier zone receives all `E_total`; upstream stays fixed and downstream shifts.
- Carrier inference uses paired visible turns first, otherwise the unique longest body interval; ambiguity is rejected.
- Marks are `p=<零件号>` at 30 mm.
- The PL splitter stays independent of BH/BOX and does not perform merge-sheet output.

- [ ] **Step 5: Run complete automated regression**

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_real_corpus.py -q
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting -q
```

Expected: all focused PL tests pass, the real corpus reports 21 successes, and the complete BH/BOX/PL splitting suite has no regression.

- [ ] **Step 6: Generate a fresh user-visible 21-part result directory**

```powershell
$env:PL_FINAL_OUTPUT = 'E:\桌面\7、折弯板拆分图(1)\7、折弯板拆分图\Python_PL拆板结果_承载区间'
if (Test-Path -LiteralPath $env:PL_FINAL_OUTPUT) {
    throw "Fresh output directory already exists: $env:PL_FINAL_OUTPUT"
}
& $env:PL_TEST_PYTHON -m steel_dxf_split.pl.cli $env:PL_REAL_SOURCE_DXF --output-dir $env:PL_FINAL_OUTPUT
```

Expected: exit code 0, 21 DXFs, `pl_split_report.json` schema 2, no rejected items, and no modification to the prior `Python_PL拆板结果` directory.

- [ ] **Step 7: Enforce the no-special-case and isolation boundaries**

```powershell
rg -n "q4-b-181|q6-b-62|q6-b-71|q6-cb-21|q7-b-21|q7-b-404|q7-b-446|q7-b-623|q7-b-628|z2-cb|z4-cb" Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl
git diff --name-only 67506669..HEAD
git status --short
```

Expected: the `rg` command has no production-code matches; the diff contains only the PL files, PL tests, the new focused plan, and repository documentation named in this plan; user drawings and regenerated outputs are untracked outside the worktree.

- [ ] **Step 8: Commit the final tests and repository changes locally**

```powershell
git add -- backend/tests/dxf_splitting/test_pl_real_corpus.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py docs/superpowers/plans/2026-08-21-pl-carrier-interval-unfolding.md Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl
git commit -m "test: prove PL carrier unfolding across corpus"
```

Do not stage the external Chinese Markdown path, sample drawings, result DXFs, or temporary conversion files. Do not push.

## Final Verification Checklist

Run immediately before claiming completion:

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_real_corpus.py -q
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting -q
git status --short --branch
git log -7 --oneline --decorate
```

Report the exact focused/full test counts, the 21/21 real-corpus result, the fresh output directory, the updated Chinese rule file, the final local commit, and confirmation that nothing was pushed.
