# PL Bent-Plate Splitting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone `steel-dxf-split-pl` Python tool that reads one or more PL source DXFs, proves the projected plate and thickness section, develops the plate with fixed `K=0.5`, and writes one validated clean DXF per part.

**Architecture:** The new `steel_dxf_split.pl` package owns PL parsing, topology, development, output, orchestration, and CLI behavior. It may reuse only the existing read-only text/INSERT helpers and part-mark layout helper; the BH/BOX pipeline, writers, release evidence, weld-allowance code, and merge utility remain unchanged and do not import PL. Tests live in the backend test tree because the vendored Stage intentionally excludes a development `tests/` directory.

**Tech Stack:** Python 3.12/3.13, ezdxf 1.4.4, Shapely 2.1.x, pytest 8/9.

## Global Constraints

- K factor is exactly `0.5`.
- `L_raw = max(L_projection, L_K, L_bom)` and `L_target` is decimal ceiling to `0.1 mm` after suppressing at most `0.000001 mm` floating noise.
- No weld, cutting, safety, or arbitrary length allowance is added.
- One global X scale is applied about the source left edge; Y coordinates and plate width are unchanged.
- A non-uniformly scaled circular arc is emitted as an exact DXF `ELLIPSE`.
- Output is R2007, millimetres, and 1:1 with manufacturing entities only on `PLATE_CUT`; the only mark is `p=<part-number>` on `PART_LABEL`.
- Output filename is `<part-number>.dxf` without `p=`.
- The PL package must not modify or enter the BH/BOX `pipeline.py` path and must not modify, import, or call `tools/merge_sheet.py`.
- Current scope is splitting only; no before/after merge image is produced.
- User sample drawings remain untracked, read-only acceptance data and are never added to Git.
- Work is committed locally only; do not push, create a remote branch, or open a PR.

## Local Execution Environment

The current Windows host has no `uv` command and its default Python is 3.14, which is outside the package contract. Before the first RED test, create a disposable Python 3.12 environment outside the repository:

```powershell
$env:PL_TEST_VENV = Join-Path $env:TEMP 'codex-pl-splitter-py312'
& 'C:\Users\李某\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv $env:PL_TEST_VENV
$env:PL_TEST_PYTHON = Join-Path $env:PL_TEST_VENV 'Scripts\python.exe'
& $env:PL_TEST_PYTHON -m pip install --disable-pip-version-check -e '.\Stages\steel_dxf_split_v1.5.2[dev]'
```

Expected: Python reports 3.12.x, `ezdxf==1.4.4`, `Shapely>=2.1,<3`, and pytest are importable. The disposable environment is not staged or committed.

---

## File Map

- Create `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/__init__.py`: stable importable PL API.
- Create `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py`: immutable source, geometry, development, result, and rejection contracts.
- Create `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/source.py`: frozen input discovery, top-level sheet expansion, text normalization, part/spec/BOM binding.
- Create `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/geometry.py`: native segment expansion, Shapely topology, main-view and section proofs, exact path lengths.
- Create `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py`: K calculation, target rounding, global longitudinal transform.
- Create `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/writer.py`: clean R2007 DXF generation, mark placement, save/reload validation.
- Create `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/compiler.py`: single-context compilation and batch transaction/report publication.
- Create `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/cli.py`: `steel-dxf-split-pl` argument parsing, JSON stdout, and exit codes 0/1/2.
- Create `Stages/steel_dxf_split_pl/`: register the independent console script in a lightweight launcher package that depends on the existing Stage package.
- Preserve `Stages/steel_dxf_split_v1.5.2/pyproject.toml`: keep the protected BH/BOX package metadata and console-script list byte-for-byte unchanged.
- Modify `Stages/steel_dxf_split_v1.5.2/README.md`: document the second independent command and explicitly state that it is not backend-integrated.
- Create `backend/tests/dxf_splitting/test_pl_splitter.py`: generated DXF fixtures and focused unit/integration/CLI regression tests.
- Modify `D:/下载/PL折弯板拆板规则总结.md`: align the user-facing rule summary with the implemented contract.

### Task 1: Freeze PL contracts and exact length arithmetic

**Files:**
- Create: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py`
- Create: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py`
- Create: `backend/tests/dxf_splitting/test_pl_splitter.py`

**Interfaces:**
- Produces: `PLSourceContext`, `PLMetadata`, `NativeSegment`, `PlateOutline`, `SectionProof`, `DevelopmentMetrics`, `DevelopedPlate`, `PLItemResult`, `PLBatchResult`, and `PLSplitError`.
- Produces: `neutral_axis_length(surface_lengths_mm)`, `ceil_tenth_mm(value_mm)`, `calculate_development(projection_length_mm, surface_lengths_mm, bom_length_mm, anchor_x_mm)`, and `transform_outline(entities, projection_length_mm, surface_lengths_mm, bom_length_mm, anchor_x_mm)`.
- `transform_outline()` returns exact transformed ezdxf entity copies together with immutable `DevelopmentMetrics`.

- [ ] **Step 1: Write failing arithmetic and transformation tests**

```python
from decimal import Decimal

import ezdxf
import pytest

from steel_dxf_split.pl.development import (
    calculate_development,
    ceil_tenth_mm,
    neutral_axis_length,
    transform_outline,
)


def test_k_half_and_three_length_authorities_use_the_largest_value():
    assert neutral_axis_length((470.0, 472.0)) == pytest.approx(471.0)
    metrics = calculate_development(
        projection_length_mm=399.0,
        surface_lengths_mm=(468.0, 472.0),
        bom_length_mm=469.4,
        anchor_x_mm=12.0,
    )
    assert metrics.k_factor == 0.5
    assert metrics.k_length_mm == pytest.approx(470.0)
    assert metrics.raw_length_mm == pytest.approx(470.0)
    assert metrics.target_length_mm == pytest.approx(470.0)
    assert metrics.scale_x == pytest.approx(470.0 / 399.0)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Decimal("10.0"), Decimal("10.0")),
        (Decimal("10.0000004"), Decimal("10.0")),
        (Decimal("10.000002"), Decimal("10.1")),
        (Decimal("10.099999"), Decimal("10.1")),
        (Decimal("10.1000011"), Decimal("10.2")),
    ],
)
def test_length_is_ceiled_to_one_decimal_without_arbitrary_allowance(source, expected):
    assert ceil_tenth_mm(source) == expected


def test_global_x_transform_anchors_left_edge_preserves_y_and_converts_arc():
    document = ezdxf.new("R2007")
    line = document.modelspace().add_line((10.0, 2.0), (20.0, 2.0))
    arc = document.modelspace().add_arc((20.0, 7.0), 5.0, 270.0, 90.0)
    transformed, metrics = transform_outline(
        (line, arc),
        projection_length_mm=20.0,
        surface_lengths_mm=(25.0, 25.0),
        bom_length_mm=20.0,
        anchor_x_mm=10.0,
    )
    assert transformed[0].dxftype() == "LINE"
    assert transformed[0].dxf.start.x == pytest.approx(10.0)
    assert transformed[0].dxf.start.y == pytest.approx(2.0)
    assert transformed[0].dxf.end.x == pytest.approx(22.5)
    assert transformed[1].dxftype() == "ELLIPSE"
    assert metrics.scale_x == pytest.approx(1.25)
```

- [ ] **Step 2: Run the focused test and verify import failure**

Run:

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py -k "k_half or length_is_ceiled or global_x_transform" -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'steel_dxf_split.pl'`.

- [ ] **Step 3: Add immutable contracts and minimal exact arithmetic**

Implement these exact typed interfaces: `neutral_axis_length(surface_lengths_mm: tuple[float, float]) -> float`; `ceil_tenth_mm(value_mm: float | Decimal) -> Decimal`; `calculate_development(*, projection_length_mm: float, surface_lengths_mm: tuple[float, float], bom_length_mm: float, anchor_x_mm: float) -> DevelopmentMetrics`; and `transform_outline(entities: Sequence[DXFGraphic], *, projection_length_mm: float, surface_lengths_mm: tuple[float, float], bom_length_mm: float, anchor_x_mm: float) -> tuple[tuple[DXFGraphic, ...], DevelopmentMetrics]`. Use `Decimal(str(value))`, subtract only a positive residual no greater than `Decimal("0.000001")` from an exact tenth, then apply `quantize(Decimal("0.1"), rounding=ROUND_CEILING)`.

Build the matrix with:

```python
Matrix44.chain(
    Matrix44.translate(-anchor_x_mm, 0.0, 0.0),
    Matrix44.scale(metrics.scale_x, 1.0, 1.0),
    Matrix44.translate(anchor_x_mm, 0.0, 0.0),
)
```

Call `ezdxf.transform.copies()`, reject any transform log entry or entity-count change, and rely on ezdxf’s exact `ARC` to `ELLIPSE` conversion.

- [ ] **Step 4: Run focused tests and verify they pass**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit locally**

```powershell
git add -- backend/tests/dxf_splitting/test_pl_splitter.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/development.py
git commit -m "feat: add exact PL development rules"
```

### Task 2: Parse independent PL source contexts and metadata

**Files:**
- Create: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/source.py`
- Modify: `backend/tests/dxf_splitting/test_pl_splitter.py`

**Interfaces:**
- Consumes: `PLSourceContext` and `PLMetadata` from Task 1.
- Produces: `discover_input_files(input_path, output_dir)`, `load_source_contexts(source_path)`, `canonical_part_number(value)`, and `extract_metadata(context)`.
- A combined modelspace with independently provable top-level sheet INSERTs yields one source context per sheet; otherwise the expanded modelspace is one context.

- [ ] **Step 1: Add failing generated-source tests**

Create a fixture builder that writes a R2007/mm DXF with `Part`, `PartMark`, and `OtherObjectType` layers. Put `q6-b-62`, `PL25*300`, and `470` on one table row; wrap the sheet in a top-level INSERT for the combined case. Assert:

```python
contexts = load_source_contexts(combined_path)
assert tuple(context.context_id for context in contexts) == ("sheet-a", "sheet-b")
metadata = extract_metadata(contexts[0])
assert metadata.part_number == "q6-b-62"
assert metadata.thickness_mm == pytest.approx(25.0)
assert metadata.width_mm == pytest.approx(300.0)
assert metadata.bom_length_mm == pytest.approx(470.0)
assert canonical_part_number("p=q6-b-62") == "q6-b-62"
```

Also assert rejection of conflicting `PartMark` values, more than one PL table row, ambiguous numeric cells on the PL row, `.dwg` input, identical/nested input-output directories, and output-directory files appearing after the frozen scan.

- [ ] **Step 2: Run source tests and verify missing symbols**

Run:

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py -k "source or metadata or input_files" -q
```

Expected: tests fail because `steel_dxf_split.pl.source` does not exist.

- [ ] **Step 3: Implement strict context and table binding**

Use `steel_dxf_split.dxf_io.load_document`, `normalize_text`, `recursive_virtual_entities`, and `iter_modelspace_entities`. The parser must:

```python
PART_NUMBER = re.compile(r"^(?:p=)?([A-Za-z0-9][A-Za-z0-9._-]*)$", re.I)
PL_SPEC = re.compile(r"^PL\s*(\d+(?:\.\d+)?)\s*[*X×]\s*(\d+(?:\.\d+)?)$", re.I)
NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
```

- take the unique normalized `PartMark` value as the part number;
- strip only a leading case-insensitive `p=` and preserve the remaining spelling;
- bind BOM length to the nearest numeric `OtherObjectType` text strictly to the right of the unique PL spec on the same row within `max(0.1, text_height * 0.25)`;
- reject conflicting repeated part/spec/length evidence instead of consulting the filename;
- preserve virtual entity geometry and lightweight provenance (`source_path`, context id, container handle, entity handle/layer/type);
- sort input paths and contexts deterministically.

- [ ] **Step 4: Run source tests and verify they pass**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit locally**

```powershell
git add -- backend/tests/dxf_splitting/test_pl_splitter.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/source.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py
git commit -m "feat: parse PL drawing evidence"
```

### Task 3: Prove the main outline and K=0.5 thickness section

**Files:**
- Create: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/geometry.py`
- Modify: `backend/tests/dxf_splitting/test_pl_splitter.py`

**Interfaces:**
- Consumes: `PLSourceContext` and `PLMetadata`.
- Produces: `expand_native_segments(entities)`, `flatten_entity(entity, sagitta_mm=0.01)`, `native_entity_length(entity)`, `analyze_geometry(context, metadata)`, and `validate_closed_outline(entities)`.
- `analyze_geometry()` returns one `PlateOutline` and one `SectionProof`; it never guesses through ambiguity.

- [ ] **Step 1: Add failing geometry proof tests**

Generate a 399 x 300 main rectangle with one internal vertical fold line and a separate closed 399 x 25 section band. Add a second sloped-end section fixture whose area is hand-derived as `9875 mm²`. Assert:

```python
outline, section = analyze_geometry(context, metadata)
assert outline.projection_length_mm == pytest.approx(399.0)
assert outline.width_mm == pytest.approx(300.0)
assert len(outline.outer_entities) == 4
assert section.k_length_mm == pytest.approx(399.0)
assert sloped_section.k_length_mm == pytest.approx(9875.0 / 25.0)
```

Add separate fixtures that must raise `PLSplitError` for a hole in the main outline, two equally credible 300 mm-wide main views, non-X main axis, disconnected main bodies, a missing section, more than two thickness-length external end caps, and a section path gap over `0.1 mm`.

- [ ] **Step 2: Run geometry tests and verify missing implementation**

Run:

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py -k "geometry or outline or section" -q
```

Expected: tests fail because geometry symbols are absent.

- [ ] **Step 3: Implement native geometry plus Shapely proof**

Expand `LWPOLYLINE` bulges into virtual `LINE`/`ARC` entities, accept native `LINE`, `ARC`, and `ELLIPSE`, and reject unsupported `Part` curves. For each `Part` connected component:

```python
linework = unary_union(LineString(flatten_entity(entity)) for entity in component)
polygons, cuts, dangles, invalid = polygonize_full(linework)
material = unary_union(tuple(polygons.geoms))
```

Snap endpoints only for component membership within `0.1 mm`; use the original entities for length and output. A main candidate is a single valid polygon with no interiors, positive area, X span greater than Y span, and Y span within `0.1 mm` of the declared width. Select source outer entities only when their flattened line is covered by a `0.05 mm` buffer of the union boundary.

For each non-main polygon candidate, require one valid hole-free material polygon whose X span matches the main projection within `0.1 mm`. Compute `L_K = section_polygon.area / metadata.thickness_mm`; for a constant-thickness band this equals the average of both plate-face paths while correctly handling the real corpus's sloped and composite end-cap chains. Build the polygon from traceable native lines/arcs using `0.001 mm` maximum sagitta and store `proof_method="section_area_over_thickness_k_half"`.

- [ ] **Step 4: Run geometry tests and verify they pass**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit locally**

```powershell
git add -- backend/tests/dxf_splitting/test_pl_splitter.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/geometry.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/contracts.py
git commit -m "feat: prove PL source geometry"
```

### Task 4: Write and revalidate a clean PL DXF

**Files:**
- Create: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/writer.py`
- Modify: `backend/tests/dxf_splitting/test_pl_splitter.py`

**Interfaces:**
- Consumes: a `DevelopedPlate` containing metadata, outline proof, section proof, transformed entities, and metrics.
- Produces: `write_pl_dxf(developed, output_path) -> PLWriteResult` and `validate_saved_pl_dxf(output_path, developed) -> PLWriteResult`.

- [ ] **Step 1: Add failing output-contract tests**

Compile a generated outline containing an arc and assert after reopening:

```python
saved = ezdxf.readfile(output_path)
assert saved.dxfversion == "AC1021"
assert saved.header["$INSUNITS"] == 4
assert {entity.dxf.layer for entity in saved.modelspace()} == {
    "PLATE_CUT",
    "PART_LABEL",
}
assert len(saved.modelspace().query('ELLIPSE[layer=="PLATE_CUT"]')) == 1
labels = list(saved.modelspace().query('TEXT[layer=="PART_LABEL"]'))
assert [label.dxf.text for label in labels] == ["p=q6-b-62"]
assert labels[0].dxf.style == "SplitChinese"
assert saved.audit().has_errors is False
```

Also assert saved outline X span equals target within `0.001 mm`, Y span equals source width within `0.001 mm`, left edge is unchanged within `0.001 mm`, and source annotation/entity layers never appear.

- [ ] **Step 2: Run writer tests and verify missing writer**

Run:

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py -k "writer or saved or output_contract" -q
```

Expected: tests fail because `write_pl_dxf` is absent.

- [ ] **Step 3: Implement isolated writer and save/reload checks**

Create a new document with `ezdxf.new("R2007", setup=False)`, set `$INSUNITS=4`, add `PLATE_CUT` ACI 7, `PART_LABEL` ACI 3, `SPLIT_NOTE` ACI 5, and `SplitChinese` using `simsun.ttc`. Add each transformed native entity directly to modelspace after setting `layer="PLATE_CUT"`.

Place the single label with:

```python
placement = layout_part_marks(
    (
        PartMarkTarget(
            target_id=metadata.part_number,
            label=f"p={metadata.part_number}",
            outer_geometry=developed_polygon,
            material_geometry=developed_polygon,
        ),
    ),
    preferred_height_mm=preferred_standard_part_mark_height(metadata.width_mm / 2.5),
)[0]
```

Use `TextEntityAlignment.MIDDLE_CENTER`, save to the supplied temporary path, reopen through `load_document()`, and run every dimensional, topology, layer/entity, label, forbidden-entity, and audit check before returning.

- [ ] **Step 4: Run writer tests and verify they pass**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit locally**

```powershell
git add -- backend/tests/dxf_splitting/test_pl_splitter.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/writer.py
git commit -m "feat: write validated PL output DXF"
```

### Task 5: Add batch compiler, independent CLI, and atomic publication

**Files:**
- Create: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/compiler.py`
- Create: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/cli.py`
- Create: `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl/__init__.py`
- Create: `Stages/steel_dxf_split_pl/pyproject.toml`
- Create: `Stages/steel_dxf_split_pl/src/steel_dxf_split_pl/cli.py`
- Preserve: `Stages/steel_dxf_split_v1.5.2/pyproject.toml`
- Modify: `backend/tests/dxf_splitting/test_pl_splitter.py`

**Interfaces:**
- Consumes: all prior source, geometry, development, and writer APIs.
- Produces: `compile_context(context, output_path)`, `split_pl(input_path, output_dir, overwrite=False) -> PLBatchResult`, and `main(argv=None) -> int`.
- Console script: `steel-dxf-split-pl = "steel_dxf_split_pl.cli:main"`; the wrapper delegates only to `steel_dxf_split.pl.cli.main`.

- [ ] **Step 1: Add failing end-to-end and CLI tests**

Assert a two-sheet combined fixture produces `q6-b-62.dxf`, `q6-b-71.dxf`, and `pl_split_report.json`, with one JSON report item per sheet. Assert existing targets fail unless `--overwrite`, one rejected sheet yields exit code 1 while valid sheets publish, invalid input/directory contracts yield exit code 2 without manufacturing output, and successful input yields exit code 0.

Patch `steel_dxf_split.pipeline.split_classified_dxf` to raise if called and assert PL compilation still succeeds. In a clean Python subprocess, import `steel_dxf_split.pl`, execute a generated PL split, and assert `sys.modules` contains no `steel_dxf_split.box` module, BH manufacturing module, `steel_dxf_split.pipeline`, or `tools.merge_sheet`; this checks the runtime isolation behavior without grepping implementation text.

- [ ] **Step 2: Run batch/CLI tests and verify missing orchestration**

Run:

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py -k "batch or cli or independent" -q
```

Expected: tests fail because compiler/CLI symbols and the independent launcher package are absent.

- [ ] **Step 3: Implement transaction and reports**

`split_pl()` must freeze all input files before opening any output, reject duplicate normalized part numbers, and create a unique temporary task directory inside `output_dir`. Each successful item is written and validated there, then published with `os.replace()` to the exact `<part-number>.dxf` target. Existing targets are left untouched unless `overwrite=True`.

Write `pl_split_report.json` in UTF-8 with `ensure_ascii=False`, including metadata, candidate counts, source handles/types, `L_projection`, section area, thickness, K proof method, `L_K`, `L_bom`, `L_raw`, `L_target`, `scale_x`, anchor, entity type counts, validation results, result path, or stable reject code plus Chinese message. Publish the report atomically after all items. Remove only the exact owned temporary directory in `finally`.

`main()` prints the same report object as JSON and returns 0 for all success, 1 when at least one auditable context is rejected, and 2 for input/whole-document failure. It never invokes DWG conversion or merge behavior.

- [ ] **Step 4: Run all PL tests and verify they pass**

Run:

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py -q
```

Expected: all PL tests pass.

- [ ] **Step 5: Commit locally**

```powershell
git add -- backend/tests/dxf_splitting/test_pl_splitter.py Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pl Stages/steel_dxf_split_pl
git commit -m "feat: add standalone PL splitter"
```

### Task 6: Document and verify without changing BH/BOX or merge behavior

**Files:**
- Modify: `Stages/steel_dxf_split_v1.5.2/README.md`
- Modify: `D:/下载/PL折弯板拆板规则总结.md`

**Interfaces:**
- Consumes: finalized public CLI and report schema.
- Produces: exact operator instructions and corrected domain rules.

- [ ] **Step 1: Update package and user documentation**

In the Stage README, retain the existing BH/BOX section and add a clearly separate PL section with:

```powershell
steel-dxf-split-pl ".\combined.dxf" --output-dir ".\pl-output"
steel-dxf-split-pl ".\single-or-batch-input" --output-dir ".\pl-output" --overwrite
```

State that PL accepts DXF only, emits one file per part plus `pl_split_report.json`, is not routed through the current backend BH/BOX workflow, and does not merge drawings.

In `D:/下载/PL折弯板拆板规则总结.md`, replace the old unknown-K and station-map conclusions with fixed K=0.5, the three-authority maximum, decimal ceiling to 0.1 mm, no extra allowance, one left-anchored global X scale, unchanged Y/width, exact ARC-to-ELLIPSE output, `PLATE_CUT`/`PART_LABEL`, and `p=` label rules. Explicitly mark merge as outside the current task.

- [ ] **Step 2: Run focused PL tests, architecture boundary tests, and BH/BOX regression tests**

Run:

```powershell
& $env:PL_TEST_PYTHON -m pytest backend/tests/dxf_splitting/test_pl_splitter.py backend/tests/architecture/test_cad_processing_boundaries.py backend/tests/dxf_splitting/test_box_release_attestation_runtime.py backend/tests/dxf_splitting/test_box_regressions.py backend/tests/dxf_splitting/test_bh_development_rounding.py -q
```

Expected: all selected tests pass or external-sample tests skip only for their already-declared missing corpus condition.

- [ ] **Step 3: Run the current 21-part real-source acceptance**

Using the existing temporary DXF conversion of `7、折弯板拆分图/折弯板合并图.dwg`, run:

```powershell
& $env:PL_TEST_PYTHON -m steel_dxf_split.pl.cli `
  "C:\Users\李某\AppData\Local\Temp\codex-pl-review-a33244fe47b24c6b9cde9747257bc0d7\dxf\combined.dxf" `
  --output-dir "$env:TEMP\codex-pl-acceptance-output"
```

Expected: exit 0, 21 success items, 21 uniquely named part DXFs, one report, no rejected item, every result audit clean, every X span equals its reported rounded target, every width is unchanged, and every label is `p=<filename-stem>`.

- [ ] **Step 4: Verify repository scope and inspect the final diff**

Run:

```powershell
git status --short
git diff --check HEAD~1..HEAD
git diff --name-only 5721010f..HEAD
```

Expected: the untracked `7、折弯板拆分图/` remains unmodified/unadded; no BH/BOX source, unified `pipeline.py`, backend integration, or `tools/merge_sheet.py` appears in the feature diff.

- [ ] **Step 5: Commit documentation locally and stop before remote operations**

```powershell
git add -- Stages/steel_dxf_split_v1.5.2/README.md
git commit -m "docs: document standalone PL splitting"
```

Do not run `git push`, create a remote branch, or open a pull request. The external Markdown file is outside this repository and must not be staged.
