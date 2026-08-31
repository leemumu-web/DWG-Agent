# PL Terminal Taper Carrier Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exclude paired terminal taper intervals from PL carrier selection while leaving all unrelated splitter behavior unchanged.

**Architecture:** Add one topology-only paired-end predicate inside `longitudinal._intervals`; when both terminal intervals have significant, opposite upper/lower Y changes, seed those two intervals as end features before the existing end-propagation loop. Keep carrier ranking, affine transformation, target calculation, geometry extraction, writing, labels, curves, BH, and BOX untouched.

**Tech Stack:** Python 3.12, ezdxf 1.4.4, Shapely 2.x, pytest, Ruff.

## Global Constraints

- Production code may modify only `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py`.
- Tests may modify only `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py` and `backend/tests/dxf_splitting/test_pl_paired_corpus.py`.
- Do not modify K factor `0.5`, tenth-millimetre ceiling, short connectors, terminal slope normalization, curve handling, labels, reports, compiler, writer, geometry, BH, BOX, or merge modules.
- The paired-end rule must depend only on interval topology and `_TURN_TOLERANCE_MM`; production code must contain no sample name, reference path, fixed coordinate, or fixed part dimension.
- All 122 baseline carrier signatures must remain unchanged except `2b1-cb-61` and `2b1-cb-62`, which must both change to carrier interval `(1,)`.
- Do not overwrite source drawings or prior output directories; do not push remotely.

---

### Task 1: Lock paired-terminal topology with RED tests

**Files:**
- Modify: `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`
- Modify: `backend/tests/dxf_splitting/test_pl_paired_corpus.py`
- Read: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py:1561-1710`

**Interfaces:**
- Consumes: `analyze_longitudinal_outline(entities, polygon, thickness_mm=...) -> LongitudinalProof`.
- Produces: anonymous positive/negative behavior tests and real-corpus carrier assertions.

- [ ] **Step 1: Capture the pre-change 122-part carrier signature**

Run this read-only command before production edits:

```powershell
$env:PYTHONPATH='Stages/steel_dxf_split_v1.5.2/src'
@'
import json
from pathlib import Path

report = Path(r'E:/桌面/PL板材/122个PL原图_最新PL拆板_20260823/pl_split_report.json')
payload = json.loads(report.read_text(encoding='utf-8'))
signatures = {
    item['part_number']: item['transform']['carrier_interval_indices']
    for item in payload['items']
    if item['status'] == 'success'
}
Path(r'E:/桌面/DWG-Agent/.tmp/pl_carrier_before_terminal_taper.json').write_text(
    json.dumps(signatures, ensure_ascii=False, indent=2),
    encoding='utf-8',
)
print(len(signatures))
'@ | python -
```

Expected: `122`.

- [ ] **Step 2: Add the anonymous paired-terminal positive test**

Append to `test_pl_carrier_unfolding.py`:

```python
def test_paired_terminal_tapers_are_end_features_not_carriers() -> None:
    entities, polygon = _paired_outline(
        (
            (0.0, 100.0),
            (300.0, 200.0),
            (2400.0, 200.0),
            (2700.0, 100.0),
        ),
        (
            (0.0, 0.0),
            (300.0, -100.0),
            (2400.0, -100.0),
            (2700.0, 0.0),
        ),
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (1,)
    assert proof.selection_reason == "unique_longest_body"
    assert proof.intervals[0].is_end_feature
    assert proof.intervals[-1].is_end_feature
```

- [ ] **Step 3: Add single-ended and same-direction negative tests**

Append to the same file:

```python
def test_single_terminal_taper_keeps_the_existing_carrier_rule() -> None:
    entities, polygon = _paired_outline(
        ((0.0, 100.0), (300.0, 200.0), (2700.0, 200.0)),
        ((0.0, 0.0), (300.0, -100.0), (2700.0, -100.0)),
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (0,)
    assert proof.selection_reason == "paired_visible_turn"
    assert not proof.intervals[0].is_end_feature


def test_same_direction_terminal_slopes_do_not_become_end_features() -> None:
    entities, polygon = _paired_outline(
        (
            (0.0, 100.0),
            (300.0, 150.0),
            (2400.0, 150.0),
            (2700.0, 100.0),
        ),
        (
            (0.0, 0.0),
            (300.0, 50.0),
            (2400.0, 50.0),
            (2700.0, 0.0),
        ),
    )

    with pytest.raises(PLSplitError) as error:
        analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert error.value.code == "CARRIER_AMBIGUOUS"
```

- [ ] **Step 4: Lock the two real double-taper carriers**

In `test_pl_paired_corpus.py`, immediately after `report` is loaded, add:

```python
    items_by_part = {item["part_number"]: item for item in report["items"]}
    assert tuple(
        items_by_part["2b1-cb-61"]["transform"]["carrier_interval_indices"]
    ) == (1,)
    assert tuple(
        items_by_part["2b1-cb-62"]["transform"]["carrier_interval_indices"]
    ) == (1,)
```

- [ ] **Step 5: Run focused tests and record RED**

```powershell
$py='C:/Users/李某/AppData/Local/Temp/codex-pl-splitter-py312/Scripts/python.exe'
$env:PYTHONPATH='Stages/steel_dxf_split_v1.5.2/src'
& $py -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -k 'paired_terminal_tapers or single_terminal_taper or same_direction_terminal' -q
```

Expected: the paired-terminal positive case fails under the old carrier selection; both negative cases retain existing behavior.

### Task 2: Implement the isolated paired-end classifier

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py:1680-1705`
- Test: `backend/tests/dxf_splitting/test_pl_carrier_unfolding.py`

**Interfaces:**
- Consumes: `LongitudinalIntervalEvidence.upper_delta_y_mm`, `lower_delta_y_mm`, `is_end_feature`, and `_TURN_TOLERANCE_MM`.
- Produces: the existing `tuple[LongitudinalIntervalEvidence, ...]` with only proven paired terminal tapers marked as end features.

- [ ] **Step 1: Add the minimal paired-terminal seed before existing propagation**

Insert after `terminal_end_indices: set[int] = set()`:

```python
    if len(intervals) > 1:
        terminal_intervals = (intervals[0], intervals[-1])
        paired_terminal_tapers = all(
            abs(interval.upper_delta_y_mm) > _TURN_TOLERANCE_MM
            and abs(interval.lower_delta_y_mm) > _TURN_TOLERANCE_MM
            and interval.upper_delta_y_mm * interval.lower_delta_y_mm < 0.0
            for interval in terminal_intervals
        )
        if paired_terminal_tapers:
            terminal_end_indices.update((0, len(intervals) - 1))
```

Leave the existing forward/reverse propagation loop and `select_carrier_zone` unchanged.

- [ ] **Step 2: Run focused GREEN tests**

```powershell
& $py -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -k 'paired_terminal_tapers or single_terminal_taper or same_direction_terminal or rounded_terminal or visible_turn' -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run the complete carrier suite**

```powershell
& $py -m pytest backend/tests/dxf_splitting/test_pl_carrier_unfolding.py -q
```

Expected: zero failures.

- [ ] **Step 4: Commit the isolated implementation**

```powershell
git add Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_paired_corpus.py
git commit -m "fix: exclude paired PL terminal tapers"
```

### Task 3: Prove non-interference and publish a fresh batch

**Files:**
- Read: `E:/桌面/DWG-Agent/.tmp/pl_carrier_before_terminal_taper.json`
- Output: `E:/桌面/PL板材/122个PL原图_端部收口修正版_20260824/`

**Interfaces:**
- Consumes: the independent PL CLI and the 122 verified DXF sources.
- Produces: 122 audited DXFs, schema-2 report, and an exact before/after carrier signature comparison.

- [ ] **Step 1: Run the 21-part and 122-pair corpora**

```powershell
$env:PL_REAL_SOURCE_DXF='E:/桌面/DWG-Agent/.tmp/pl_merge_dxf_20260822/merge.dxf'
$env:PL_REAL_REFERENCE_DIR='E:/桌面/DWG-Agent/.tmp/pl_dxf_batch_20260822'
& $py -m pytest backend/tests/dxf_splitting/test_pl_real_corpus.py -q --log-disable=ezdxf
$env:PL_PAIRED_SOURCE_DIR='E:/桌面/DWG-Agent/.tmp/pl_pairs_20260822/dxf_source'
$env:PL_PAIRED_REFERENCE_DIR='E:/桌面/DWG-Agent/.tmp/pl_pairs_20260822/dxf_result'
& $py -m pytest backend/tests/dxf_splitting/test_pl_paired_corpus.py -q --log-disable=ezdxf
```

Expected: 21/21 and 122/122, zero rejects.

- [ ] **Step 2: Run all DXF splitting tests and static checks**

```powershell
& $py -m pytest backend/tests/dxf_splitting -q --log-disable=ezdxf
& $py -m py_compile Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py
& $py -m ruff check Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_paired_corpus.py
& $py -m ruff format --check Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/longitudinal.py backend/tests/dxf_splitting/test_pl_carrier_unfolding.py backend/tests/dxf_splitting/test_pl_paired_corpus.py
git diff --check
```

Expected: zero failures, clean static checks, and no diff whitespace errors.

- [ ] **Step 3: Publish a non-overwriting 122-part batch**

```powershell
$target='E:/桌面/PL板材/122个PL原图_端部收口修正版_20260824'
if (Test-Path -LiteralPath $target) { throw "target already exists: $target" }
& $py -m steel_dxf_split.pl.cli 'E:/桌面/DWG-Agent/.tmp/pl_pairs_20260822/dxf_source' --output-dir $target
```

Expected: `success_count=122`, `rejected_count=0`, and 122 DXFs.

- [ ] **Step 4: Compare all carrier signatures exactly**

```python
import json
from pathlib import Path

before = json.loads(
    Path(r"E:/桌面/DWG-Agent/.tmp/pl_carrier_before_terminal_taper.json").read_text(
        encoding="utf-8"
    )
)
after_payload = json.loads(
    Path(
        r"E:/桌面/PL板材/122个PL原图_端部收口修正版_20260824/pl_split_report.json"
    ).read_text(encoding="utf-8")
)
after = {
    item["part_number"]: item["transform"]["carrier_interval_indices"]
    for item in after_payload["items"]
    if item["status"] == "success"
}
changed = {name: (before[name], after[name]) for name in before if before[name] != after[name]}
assert changed == {
    "2b1-cb-61": ([0, 1, 2], [1]),
    "2b1-cb-62": ([0], [1]),
}
```

Expected: exactly two intentional changes and zero changes among the other 120 parts.

- [ ] **Step 5: Audit the fresh outputs**

Reopen every DXF and assert one `PART_LABEL` with `p=` prefix, no audit errors, no `POINT` in `PLATE_CUT`, target-length agreement within 0.001 mm, and no zero-length LINE. For `2b1-cb-62`, assert the output is 2757.0 mm long and its two terminal slopes match the source terminal slopes within `1e-6` direction-vector tolerance.

- [ ] **Step 6: Final local commit if validation required test adjustments**

```powershell
git add backend/tests/dxf_splitting/test_pl_paired_corpus.py
git commit -m "test: lock paired PL terminal tapers"
```

Skip this commit when the worktree is already clean. Never push remotely.

