# BOX 独立多视图重建实施计划

> 下方逐步清单保留为设计和 TDD 过程记录，不作为最终完成状态。用户选择在当前任务内
> 直接执行，不分派子代理。
>
> 2026-07-19 补充：原 v2/v3 REGION 审计曾在 `2b2-cb-145` 和
> `2b2-cb-155` 各漏一张真实成品板，原 20/20 证据已作废。清单中重新成立的
> 20/20 以 `2026-07-19-box-region-proof-correction.md` 的修复后 proof 为准。

**目标：** 建立 BOX 独立 DXF compiler，从直接轮廓或正投影证据重建四块制造板，
不调用旧 BOX extractor 或任何 BH 领域模块。

**架构：** `BOX facts → metadata evidence → geometry IR → view IR → reconstruction
hypotheses → complete four-plate solver → BoxManufacturingIR → SplitAssembly adapter
→ atomic writer`。20 组前后 DXF 只用于离线校准与验收，生产代码不读取拆板后 DXF。

**技术栈：** Python 3.12、ezdxf 1.4、Shapely 2.1、pytest 8/9、Windows PowerShell。

## 2026-07-18 执行结果

- [x] BOX runtime 与旧 extractor、`bh_*` 领域代码解耦；
- [x] 投影闭环、站位轨道、端链、developed rails 和实例级四板重建；
- [x] 完整四角色有限域求解、孔归属、唯一解和 fail-closed 门禁；
- [x] 平移、90° 旋转、实体倒序、handle/path 改变的制造语义不变量；
- [x] BOX 核心测试 184/184；
- [x] 20/20 solver、结构真值和连续几何阈值匹配；
- [x] 毫米单位门禁、不支持实体拒绝、保存后几何/孔/单位回读验证；
- [x] 块扁平化、3/9 个通用图层分散、独立视图小角度旋转和孔观测实例数变形测试；
- [x] 视图 runner-up 安全分差、topology 有限预算、闭合 LWPOLYLINE 投影视图、
  多站位纸面配准、标注圆歧义、HATCH 边界上下文拒绝和标签—板件绑定负例；
- [x] gate proof 绑定实现/依赖/corpus，监督评估逐对写出并重新打开 DXF；
- [x] 迁移矩阵、v3 机器报告和验证报告；
- [ ] 人工冻结监督 manifest 和 production proof。该项属于生产授权，不由算法提交
  自动完成。

## Global Constraints

- `D:\Dev\Projects\cad Agent` is strictly read-only.
- Do not call `extract_box_assembly()`, `parse_metadata()`, or any `bh_*` domain function from BOX runtime or BOX supervision.
- BH is a design-pattern reference only; BOX facts, view semantics, reconstruction, solver, tolerances, and truth data are independent.
- DXF handles are diagnostics only and must not affect identity, ordering, fingerprints, or decisions.
- Text evidence must never authorize geometry by itself.
- bbox is prefilter-only; final containment and ownership use Shapely topology.
- Unknown topology, missing evidence, non-equivalent multiple solutions, or ambiguous hole ownership fail closed.
- Do not install dependencies or rebuild `.venv`. The user later authorized one
  scoped local commit for this BOX solution; remote push and history rewriting
  remain prohibited.
- Run tests with the existing cp312 packages and bundled Python:

```powershell
$env:PYTHONPATH='src;.venv\Lib\site-packages;D:\anaconda3\Lib\site-packages'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
& 'C:\Users\lsp19\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest <tests> -q
```

---

## File Structure

Create:

- `src/steel_dxf_split/dxf_io.py` — neutral DXF loading.
- `src/steel_dxf_split/profile_detection.py` — neutral BH/BOX routing only.
- `src/steel_dxf_split/box_metadata.py` — BOX row/profile/scale evidence resolution.
- `src/steel_dxf_split/box_view_ir.py` — topology-derived view candidates and station coordinates.
- `src/steel_dxf_split/box_reconstruction.py` — direct and multiview plate hypotheses with edge provenance.
- `src/steel_dxf_split/box_manufacturing.py` — authoritative manufacturing IR, contract, and `SplitAssembly` adapter.
- `src/steel_dxf_split/box_compiler.py` — BOX compiler orchestration.
- `src/steel_dxf_split/reference_geometry.py` — domain-neutral manual-DXF polygonization for offline comparison.
- `tests/test_box_architecture_v2.py`
- `tests/test_box_metadata_v2.py`
- `tests/test_box_view_ir_v2.py`
- `tests/test_box_reconstruction_v2.py`
- `tests/test_box_manufacturing_v2.py`
- `tests/test_box_compiler_v2.py`
- `tests/test_box_metamorphic_v2.py`
- `tests/test_box_supervised_pairs_v2.py`

Modify:

- `src/steel_dxf_split/box_geometry_ir.py` — retain open chains and geometric clusters.
- `src/steel_dxf_split/box_solver.py` — solve real reconstruction hypotheses and normalize pair symmetry.
- `src/steel_dxf_split/box_supervision.py` — compile with BOX compiler and compare through neutral reference geometry.
- `src/steel_dxf_split/pipeline.py` — make BOX compiler authoritative.
- `src/steel_dxf_split/bh_pipeline.py` — import neutral DXF loader.
- `src/steel_dxf_split/bh_text.py` — retain BH parsing but no longer own family routing.
- Existing BOX tests — remove legacy extractor expectations and retain only neutral writer compatibility tests.
- `docs/BOX_CAD_Agent只读迁移审计与DXF迁移矩阵.md`
- `docs/superpowers/reports/2026-07-18-box-dxf-native-validation.md`

---

### Task 1: Enforce the BOX Architecture Boundary

**Files:**

- Create: `src/steel_dxf_split/dxf_io.py`
- Create: `src/steel_dxf_split/profile_detection.py`
- Create: `tests/test_box_architecture_v2.py`
- Modify: `src/steel_dxf_split/pipeline.py`
- Modify: `src/steel_dxf_split/bh_pipeline.py`

**Interfaces:**

- Produces: `load_document(path: Path) -> ezdxf.document.Drawing`
- Produces: `detect_profile_family(doc: Drawing) -> str | None`
- Enforces: no BOX runtime/supervision module imports `extractor`, `bh_*`, or `text.parse_metadata`

- [ ] **Step 1: Write the architecture guard**

```python
from pathlib import Path
import ast

PACKAGE = Path(__file__).parents[1] / "src" / "steel_dxf_split"
BOX_RUNTIME = (
    "box_compiler.py",
    "box_facts.py",
    "box_geometry_ir.py",
    "box_metadata.py",
    "box_view_ir.py",
    "box_reconstruction.py",
    "box_solver.py",
    "box_manufacturing.py",
    "box_supervision.py",
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module.lstrip("."))
        elif isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
    return result


def test_box_runtime_has_no_legacy_or_bh_domain_imports() -> None:
    violations: dict[str, list[str]] = {}
    for name in BOX_RUNTIME:
        path = PACKAGE / name
        if not path.exists():
            continue
        bad = sorted(
            module
            for module in imported_modules(path)
            if module == "extractor"
            or module.endswith(".extractor")
            or module.startswith("bh_")
            or ".bh_" in module
        )
        if bad:
            violations[name] = bad
    assert violations == {}
```

- [ ] **Step 2: Run the guard and confirm RED**

Run:

```powershell
& $python -m pytest tests\test_box_architecture_v2.py -q
```

Expected: FAIL showing imports from `extractor` and `bh_compare`.

- [ ] **Step 3: Add neutral I/O and routing**

```python
# dxf_io.py
from pathlib import Path
import ezdxf


def load_document(path: Path) -> ezdxf.document.Drawing:
    if not path.is_file():
        raise FileNotFoundError(path)
    document = ezdxf.readfile(path)
    auditor = document.audit()
    if auditor.has_errors:
        raise ValueError(f"DXF audit failed with {len(auditor.errors)} errors: {path}")
    return document
```

```python
# profile_detection.py
import re
import ezdxf
from .text import normalize_text, recursive_virtual_entities

_BH_PROFILE = re.compile(
    r"\b(?:BH|WH|HW|HM|HN|H)\s*\d+(?:\.\d+)?"
    r"(?:\s*[-~]\s*\d+(?:\.\d+)?)?\s*[*X×]\s*\d+(?:\.\d+)?"
    r"\s*[*X×]\s*\d+(?:\.\d+)?\s*[*X×]\s*\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)
_BOX_PROFILE = re.compile(
    r"\bBOX\s*\d+(?:\.\d+)?\s*[*X×]\s*\d+(?:\.\d+)?"
    r"\s*[*X×]\s*\d+(?:\.\d+)?\s*[*X×]\s*\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)


def detect_profile_family(doc: ezdxf.document.Drawing) -> str | None:
    for entity in doc.modelspace():
        expanded = (
            recursive_virtual_entities(entity)
            if entity.dxftype() == "INSERT"
            else (entity,)
        )
        for item in expanded:
            if item.dxftype() not in {"TEXT", "MTEXT"}:
                continue
            raw = item.dxf.text if item.dxftype() == "TEXT" else item.plain_text()
            value = normalize_text(str(raw))
            if _BOX_PROFILE.search(value):
                return "BOX"
            if _BH_PROFILE.search(value):
                return "BH"
    return None
```

Update `pipeline.py` and `bh_pipeline.py` to import `load_document` from
`dxf_io.py`; update `pipeline.py` to import `detect_profile_family` from
`profile_detection.py`.

- [ ] **Step 4: Run architecture and BH routing tests**

Run:

```powershell
& $python -m pytest tests\test_box_architecture_v2.py tests\test_bh_compiler_v08.py -q
```

Expected: PASS.

- [ ] **Step 5: Review checkpoint**

Run `git diff --check` and `git status --short`. Confirm CAD Agent was not
written. Do not commit.

---

### Task 2: Resolve BOX Metadata Without the Legacy Material Parser

**Files:**

- Create: `src/steel_dxf_split/box_metadata.py`
- Create: `tests/test_box_metadata_v2.py`
- Modify: `src/steel_dxf_split/box_text_evidence.py`

**Interfaces:**

- Consumes: `BoxSourceFactsV1`, `tuple[BoxTextEvidence, ...]`
- Produces: `BoxProfileSpec`
- Produces: `BoxMetadataEvidence`
- Produces: `resolve_box_metadata(facts, evidence) -> BoxMetadataEvidence`

- [ ] **Step 1: Write failing metadata tests**

Use one synthetic row and the real read-only `2b1-cb-56` source. Assert:

```python
def test_resolves_box_row_by_scope_and_row_alignment() -> None:
    metadata = resolve_box_metadata(facts, classify_box_texts(facts.texts))
    assert metadata.part_number == "2b1-cb-56"
    assert metadata.profile.raw_text == "BOX1100*1100*60*60"
    assert metadata.profile.dimensions == (1100.0, 1100.0, 60.0, 60.0)
    assert metadata.nominal_length_mm == 7092.0
    assert metadata.material == "Q420GJC-Z25"
    assert metadata.drawing_scale == 20.0
    assert metadata.complete
    assert all(metadata.field_sources.values())


def test_text_only_metadata_does_not_authorize_geometry() -> None:
    metadata = resolve_box_metadata(facts_without_primitives, evidence)
    assert metadata.complete
    assert metadata.can_authorize_geometry is False


def test_conflicting_same_rank_rows_fail_closed() -> None:
    with pytest.raises(ValueError, match="ambiguous BOX metadata rows"):
        resolve_box_metadata(conflicting_facts, evidence)
```

- [ ] **Step 2: Run and confirm RED**

Run:

```powershell
& $python -m pytest tests\test_box_metadata_v2.py -q
```

Expected: FAIL because `box_metadata` does not exist.

- [ ] **Step 3: Implement typed evidence and deterministic row selection**

```python
@dataclass(frozen=True, slots=True)
class BoxProfileSpec:
    raw_text: str
    dimension_1_mm: float
    dimension_2_mm: float
    thickness_1_mm: float
    thickness_2_mm: float
    source_key: str

    @property
    def dimensions(self) -> tuple[float, float, float, float]:
        return (
            self.dimension_1_mm,
            self.dimension_2_mm,
            self.thickness_1_mm,
            self.thickness_2_mm,
        )


@dataclass(frozen=True, slots=True)
class BoxMetadataEvidence:
    part_number: str
    profile: BoxProfileSpec
    nominal_length_mm: float
    material: str
    drawing_scale: float
    field_sources: dict[str, tuple[str, ...]]
    diagnostics: dict[str, object]
    can_authorize_geometry: bool = False

    @property
    def complete(self) -> bool:
        return (
            bool(self.part_number and self.material)
            and self.nominal_length_mm > 0
            and self.drawing_scale > 0
        )
```

Row selection algorithm:

1. Parse every BOX profile text.
2. Restrict row peers to the same `source_scope`.
3. Define row tolerance as `max(profile.height * 1.5, median peer height * 1.5)`.
4. Require, in increasing local-X order, exactly one best part identity before
   the profile, one numeric length after it, one material after length, and one
   scale after material.
5. Rank rows by complete-field count, normalized Y residual, then source keys.
6. If the best two complete rows differ in field values but have equal geometric
   rank within `1e-9`, raise `ValueError`.
7. Never consult the filename.

- [ ] **Step 4: Run unit and all 20 metadata checks**

Run:

```powershell
& $python -m pytest tests\test_box_metadata_v2.py tests\test_box_text_evidence_v1.py -q
```

Expected: PASS, including parameterized assertions for all 20 source rows.

---

### Task 3: Promote Open Projection Chains into BOX Geometry IR

**Files:**

- Modify: `src/steel_dxf_split/box_geometry_ir.py`
- Modify: `tests/test_box_geometry_ir_v1.py`
- Create: `tests/test_box_view_ir_v2.py`

**Interfaces:**

- Produces: `BoxProjectionChain`
- Produces: `BoxGeometryCluster`
- Extends: `BoxGeometryIR.open_chains`, `BoxGeometryIR.clusters`

- [ ] **Step 1: Write failing open-chain and invariance tests**

```python
def test_open_parallel_projection_lines_survive_geometry_ir() -> None:
    geometry = build_box_geometry_ir(facts_for_two_open_parallel_lines())
    assert len(geometry.candidates) == 0
    assert len(geometry.open_chains) == 2
    assert len(geometry.clusters) == 1
    assert {key for chain in geometry.open_chains for key in chain.source_keys} == {
        first_key,
        second_key,
    }


def test_translation_and_rotation_preserve_cluster_signature() -> None:
    original = build_box_geometry_ir(facts)
    transformed = build_box_geometry_ir(rotate_translate_facts(facts, 90.0, 200.0, -40.0))
    assert normalized_cluster_signatures(original) == normalized_cluster_signatures(transformed)
```

- [ ] **Step 2: Run and confirm RED**

Run:

```powershell
& $python -m pytest tests\test_box_geometry_ir_v1.py tests\test_box_view_ir_v2.py -q
```

Expected: FAIL because open chains and clusters are not represented.

- [ ] **Step 3: Add immutable projection geometry**

```python
@dataclass(frozen=True, slots=True)
class BoxProjectionChain:
    chain_id: str
    line: LineString
    closed: bool
    source_keys: tuple[str, ...]
    source_scope: str


@dataclass(frozen=True, slots=True)
class BoxGeometryCluster:
    cluster_id: str
    source_scope: str
    chain_ids: tuple[str, ...]
    polygon_ids: tuple[str, ...]
    bounds: tuple[float, float, float, float]
    centroid: tuple[float, float]
    long_axis: tuple[float, float]
    longitudinal_span: float
    transverse_span: float
```

Build chains by endpoint graph per source scope. Connected components become
clusters. Calculate axes from the minimum rotated rectangle, normalize the axis
sign lexicographically, and derive IDs from normalized geometry plus source
keys. Keep current closed candidates and hole evidence unchanged.

- [ ] **Step 4: Run geometry tests**

Run:

```powershell
& $python -m pytest tests\test_box_geometry_ir_v1.py tests\test_box_view_ir_v2.py -q
```

Expected: PASS.

---

### Task 4: Build Position-Independent View Hypotheses

**Files:**

- Create: `src/steel_dxf_split/box_view_ir.py`
- Expand: `tests/test_box_view_ir_v2.py`

**Interfaces:**

- Consumes: `BoxGeometryIR`, `BoxMetadataEvidence`
- Produces: `BoxViewCandidate`, `BoxViewPairing`, `BoxViewIR`
- Produces: `build_box_view_ir(geometry, metadata) -> BoxViewIR`

- [ ] **Step 1: Write failing view-enumeration tests**

Test that:

- a long-by-`dimension_1` cluster is a candidate longitudinal projection;
- a long-by-`dimension_2` cluster is another candidate projection;
- a compact four-strip cluster is a section candidate;
- swapping paper positions does not change normalized pairing signatures;
- two equally valid non-equivalent pairings remain present and are not silently
  resolved by X/Y location.

- [ ] **Step 2: Run and confirm RED**

Run:

```powershell
& $python -m pytest tests\test_box_view_ir_v2.py -q
```

Expected: FAIL because `box_view_ir` does not exist.

- [ ] **Step 3: Implement candidate roles and station frames**

```python
class BoxViewRole(str, Enum):
    LONGITUDINAL_DIMENSION_1 = "longitudinal_dimension_1"
    LONGITUDINAL_DIMENSION_2 = "longitudinal_dimension_2"
    SECTION = "section"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BoxStationFrame:
    origin: tuple[float, float]
    longitudinal_axis: tuple[float, float]
    transverse_axis: tuple[float, float]
    station_min: float
    station_max: float


@dataclass(frozen=True, slots=True)
class BoxViewCandidate:
    view_id: str
    cluster_id: str
    possible_roles: tuple[BoxViewRole, ...]
    frame: BoxStationFrame
    hard_reasons: tuple[str, ...]
    residuals: dict[str, float]
    source_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BoxViewPairing:
    pairing_id: str
    assignments: dict[BoxViewRole, str]
    station_transforms: dict[str, tuple[float, float]]
    residual: float
    source_keys: tuple[str, ...]
```

Enumerate roles using dimensionless span residuals. Pair longitudinal views by
normalized station events: endpoints, non-collinear breakpoints, arc endpoints,
and hole stations. A pairing may be ranked by residual but is not discarded
until complete manufacturing lowering fails.

- [ ] **Step 4: Run tests and inspect all 20 view summaries**

Run:

```powershell
& $python -m pytest tests\test_box_view_ir_v2.py -q
```

Expected: PASS. Generate a read-only JSON census under
`docs/superpowers/reports/` containing view counts, possible roles, residuals,
and rejection reasons for all 20 before-DXFs.

---

### Task 5: Reconstruct Plate Polygons with Edge-Level Provenance

**Files:**

- Create: `src/steel_dxf_split/box_reconstruction.py`
- Create: `tests/test_box_reconstruction_v2.py`

**Interfaces:**

- Consumes: facts, geometry, metadata, view IR
- Produces: `BoxPlateHypothesis`
- Produces: `reconstruct_box_plate_hypotheses(...) -> tuple[BoxPlateHypothesis, ...]`

- [ ] **Step 1: Write failing direct and multiview reconstruction tests**

Required cases:

1. Direct closed Polygon retains its exact normalized boundary.
2. Two longitudinal physical edges plus a section-proven transverse span create
   a rectangular manufacturing Polygon.
3. A sloped end in the longitudinal view appears in the reconstructed Polygon.
4. BOX profile text without projection geometry yields zero hypotheses.
5. Every exterior segment has at least one physical source key and a derivation
   from the allowed set.
6. A competing second transverse span remains a separate hypothesis.

- [ ] **Step 2: Run and confirm RED**

Run:

```powershell
& $python -m pytest tests\test_box_reconstruction_v2.py -q
```

Expected: FAIL because reconstruction types do not exist.

- [ ] **Step 3: Implement reconstruction contracts**

```python
class BoxReconstructionMode(str, Enum):
    DIRECT_CONTOUR = "direct_contour"
    MULTI_VIEW = "multi_view_reconstruction"


class BoxEdgeDerivation(str, Enum):
    DIRECT_PHYSICAL = "direct_physical"
    PROJECTED_INTERSECTION = "projected_intersection"
    PROJECTION_PLUS_SECTION_SPAN = "projection_plus_section_span"


@dataclass(frozen=True, slots=True)
class BoxEdgeProvenance:
    edge_index: int
    derivation: BoxEdgeDerivation
    source_keys: tuple[str, ...]
    view_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BoxPlateHypothesis:
    hypothesis_id: str
    role_family: str
    mode: BoxReconstructionMode
    polygon: Polygon
    thickness_mm: float
    holes: tuple[Polygon, ...]
    edge_provenance: tuple[BoxEdgeProvenance, ...]
    source_keys: tuple[str, ...]
    view_pairing_id: str
    hard_reasons: tuple[str, ...]
    residuals: dict[str, float]
    soft_evidence: float
```

Direct hypotheses map boundary segments to contributing physical primitives.
Multiview hypotheses build station/transverse coordinates, materialize the
Polygon, transform it to physical millimetres, and reject any edge without an
allowed derivation. Do not synthesize candidates when there is no physical
longitudinal geometry.

- [ ] **Step 4: Implement unique hole projection**

Match hole events by normalized station, radius, and compatible view pairing.
Use Shapely material coverage and boundary clearance for final ownership.
Return hard reasons:

- `hole_projection_unmatched`
- `hole_ownership_ambiguous`
- `hole_outside_material`
- `hole_intersects_boundary`

- [ ] **Step 5: Run reconstruction and geometry tests**

Run:

```powershell
& $python -m pytest tests\test_box_reconstruction_v2.py tests\test_box_geometry_ir_v1.py -q
```

Expected: PASS.

---

### Task 6: Solve Complete BOX Manufacturing Assemblies

**Files:**

- Modify: `src/steel_dxf_split/box_solver.py`
- Create: `src/steel_dxf_split/box_manufacturing.py`
- Create: `tests/test_box_manufacturing_v2.py`
- Modify: `tests/test_box_solver_v1.py`

**Interfaces:**

- Consumes: `tuple[BoxPlateHypothesis, ...]`, metadata
- Produces: `BoxCompleteSolution`, `BoxSolverResult`
- Produces: `BoxManufacturingIR`
- Produces: `to_split_assembly(manufacturing, full_role_names) -> SplitAssembly`

- [ ] **Step 1: Write failing complete-solution tests**

Cover:

- one flange archetype and one web archetype can produce two proven symmetric
  instances each;
- duplication without a proven BOX section topology is rejected;
- upper/lower and left/right swaps normalize to one manufacturing solution;
- a geometrically distinct fifth hypothesis causes `manual_review` when the
  score margin is insufficient;
- solver-selected Polygon coordinates exactly equal the manufacturing IR;
- all holes remain attached to the selected physical instance.

- [ ] **Step 2: Run and confirm RED**

Run:

```powershell
& $python -m pytest tests\test_box_solver_v1.py tests\test_box_manufacturing_v2.py -q
```

Expected: FAIL under the dimension-only current solver.

- [ ] **Step 3: Replace dimension-only candidates with real hypotheses**

`BoxCompleteSolution.assignments` maps each role to a plate hypothesis ID.
Normalize a solution key as:

```python
(
    tuple(sorted((upper_flange_id, lower_flange_id))),
    tuple(sorted((left_web_id, right_web_id))),
    normalized_geometry_fingerprints,
)
```

Hard checks:

- all four roles;
- valid Polygon and positive thickness;
- profile/section residual within frozen policy;
- station consistency;
- complete edge provenance;
- unique hole ownership;
- section topology permits instance multiplicity;
- no contradictory use of physical source evidence.

- [ ] **Step 4: Build authoritative manufacturing IR**

```python
@dataclass(frozen=True, slots=True)
class BoxManufacturedPlate:
    role: PlateRole
    thickness_mm: float
    polygon: Polygon
    holes: tuple[Polygon, ...]
    hypothesis_id: str
    source_keys: tuple[str, ...]
    edge_provenance: tuple[BoxEdgeProvenance, ...]


@dataclass(frozen=True, slots=True)
class BoxManufacturingIR:
    metadata: BoxMetadataEvidence
    plates: tuple[BoxManufacturedPlate, ...]
    facts_fingerprint: str
    solver: BoxSolverResult
    contract: dict[str, object]
    diagnostics: dict[str, object]
```

The `SplitAssembly` adapter converts Shapely exterior coordinates and circular
holes after the manufacturing contract passes. It must not recompute or replace
selected geometry.

- [ ] **Step 5: Run solver/manufacturing tests**

Run:

```powershell
& $python -m pytest tests\test_box_solver_v1.py tests\test_box_manufacturing_v2.py -q
```

Expected: PASS.

---

### Task 7: Make the BOX Compiler Authoritative

**Files:**

- Create: `src/steel_dxf_split/box_compiler.py`
- Create: `tests/test_box_compiler_v2.py`
- Modify: `src/steel_dxf_split/pipeline.py`
- Retire from authority: `src/steel_dxf_split/box_native.py`
- Replace: `src/steel_dxf_split/box_contracts.py`
- Modify existing BOX integration tests

**Interfaces:**

- Produces: `BoxCompileResult`
- Produces: `compile_box_document(doc, source_id=None) -> BoxCompileResult`
- BOX pipeline consumes only `BoxCompileResult.assembly`

- [ ] **Step 1: Write the failing authoritative-path test**

```python
def test_box_pipeline_uses_compiler_output_without_legacy_extractor(
    monkeypatch, source, tmp_path
) -> None:
    import steel_dxf_split.extractor as legacy

    monkeypatch.setattr(
        legacy,
        "extract_box_assembly",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy extractor called")
        ),
    )
    result = split_dxf(source, tmp_path)
    assert result.report["box_compiler"]["schema_version"] == "BOX-COMPILER-2.0"
```

- [ ] **Step 2: Run and confirm RED**

Run:

```powershell
& $python -m pytest tests\test_box_compiler_v2.py -q
```

Expected: FAIL because `_split_box_dxf` calls the legacy extractor first.

- [ ] **Step 3: Implement compiler orchestration**

```python
@dataclass(frozen=True, slots=True)
class BoxCompileResult:
    facts: BoxSourceFactsV1
    metadata: BoxMetadataEvidence
    geometry: BoxGeometryIR
    views: BoxViewIR
    hypotheses: tuple[BoxPlateHypothesis, ...]
    manufacturing: BoxManufacturingIR
    assembly: SplitAssembly


def compile_box_document(
    doc: ezdxf.document.Drawing,
    *,
    source_id: str | None = None,
    full_role_names: bool = False,
) -> BoxCompileResult:
    facts = build_box_source_facts(doc, source_id=source_id)
    text_evidence = classify_box_texts(facts.texts)
    metadata = resolve_box_metadata(facts, text_evidence)
    geometry = build_box_geometry_ir(facts)
    views = build_box_view_ir(geometry, metadata)
    hypotheses = reconstruct_box_plate_hypotheses(
        facts, geometry, metadata, views
    )
    manufacturing = solve_box_manufacturing(
        facts, metadata, geometry, views, hypotheses
    )
    assembly = to_split_assembly(
        manufacturing, full_role_names=full_role_names
    )
    return BoxCompileResult(
        facts, metadata, geometry, views, hypotheses, manufacturing, assembly
    )
```

- [ ] **Step 4: Switch pipeline and reports**

`_split_box_dxf` must:

1. load with `dxf_io.load_document`;
2. call `compile_box_document`;
3. validate the returned assembly;
4. enforce the supervised proof only for `require_auto_accept`;
5. stage and validate all requested outputs;
6. report compiler facts, views, hypotheses, solution and contract;
7. atomically promote outputs.

Delete calls to `compile_box_native_evidence()` from the authoritative path.

- [ ] **Step 5: Run architecture, compiler, atomicity, and writer tests**

Run:

```powershell
& $python -m pytest tests\test_box_architecture_v2.py tests\test_box_compiler_v2.py tests\test_box_atomic_pipeline_v1.py tests\test_box_gate_integration_v1.py -q
```

Expected: PASS.

---

### Task 8: Decouple and Run the 20-Pair Supervised Gate

**Files:**

- Create: `src/steel_dxf_split/reference_geometry.py`
- Modify: `src/steel_dxf_split/box_supervision.py`
- Create: `tests/test_box_supervised_pairs_v2.py`
- Modify: `tests/test_box_supervision_v1.py`

**Interfaces:**

- Produces: `polygonize_reference_dxf(path) -> tuple[Polygon, ...]`
- Supervision consumes: `compile_box_document(before_doc)`
- Production compiler never consumes after-DXF or thresholds from acceptance

- [ ] **Step 1: Write failing independence and leakage tests**

Assert:

- `box_supervision.py` imports neither `bh_compare` nor `extractor`;
- changing after-DXF changes only evaluation verdict, never compiler output;
- acceptance thresholds cannot be mutated during evaluation;
- manifest partitions cover 10 calibration and 10 acceptance pairs.

- [ ] **Step 2: Run and confirm RED**

Run:

```powershell
& $python -m pytest tests\test_box_supervision_v1.py tests\test_box_supervised_pairs_v2.py -q
```

Expected: FAIL because supervision calls `extract_box_assembly` and
`bh_compare.polygonize_reference`.

- [ ] **Step 3: Extract neutral reference geometry**

Move the domain-neutral DXF polygonization behavior into
`reference_geometry.py`. It may use ezdxf and Shapely only. It must:

- read closed `PLATE_CUT` polylines preferentially;
- otherwise polygonize physical linework;
- normalize Polygon orientation;
- reject invalid or empty reference geometry;
- return deterministic area/bounds ordering.

- [ ] **Step 4: Compile each before-DXF through BOX compiler**

`evaluate_box_supervised_pair()` calls:

```python
document = load_document(before)
compiled = compile_box_document(
    document,
    source_id=entry.before_path,
    full_role_names=True,
)
comparison = compare_box_plates_to_manual(
    compiled.assembly.plates,
    after,
    thresholds,
)
```

Record metadata, view assignments, hypothesis count, solver disposition,
manufacturing contract, topology comparison, and failure reasons.

- [ ] **Step 5: Run calibration then frozen acceptance**

Generate:

- `docs/superpowers/reports/2026-07-18-box-view-corpus-census.json`
- `docs/superpowers/reports/2026-07-18-box-calibration-results.json`
- `docs/superpowers/reports/2026-07-18-box-acceptance-results.json`

Do not mark the manifest frozen or human-approved without explicit user
approval. A failing pair keeps the production gate unverified.

---

### Task 9: Prove Generality and Finish Documentation

**Files:**

- Create: `tests/test_box_metamorphic_v2.py`
- Modify: `docs/BOX_CAD_Agent只读迁移审计与DXF迁移矩阵.md`
- Modify: `docs/superpowers/reports/2026-07-18-box-dxf-native-validation.md`

**Interfaces:**

- Produces: normalized manufacturing semantic fingerprint
- Produces: final migration matrix and known-domain report

- [ ] **Step 1: Add metamorphic tests**

For synthetic and representative calibration drawings, apply:

- translation;
- 90/180-degree rotation;
- view rearrangement;
- entity reordering;
- handle regeneration;
- block renaming;
- equivalent block nesting;
- unit/scale conversion;
- calibrated endpoint perturbation;
- auxiliary-line insertion.

Assert identical normalized plate geometry, role-pair equivalence, holes,
thicknesses, and manufacturing semantic fingerprint.

- [ ] **Step 2: Add adversarial fail-closed tests**

Assert `manual_review` or `reject` for:

- missing section proof;
- unmatched projection;
- equally valid non-equivalent pairing;
- fifth competing reconstruction;
- ambiguous hole owner;
- unknown physical linework touching a selected boundary;
- inconsistent units or scale.

- [ ] **Step 3: Run the complete test suite**

Run:

```powershell
& $python -m pytest -q
```

Expected: all tests pass. Record exact count and duration.

- [ ] **Step 4: Run static and repository checks**

```powershell
& $python -m compileall -q src tests
git diff --check
git status --short
git -C 'D:\Dev\Projects\cad Agent' status --short
```

Expected:

- compileall exit 0;
- diff check exit 0;
- no task-caused writes in CAD Agent;
- unrelated existing DXF project changes remain preserved.

- [ ] **Step 5: Update the migration matrix**

The final table must contain:

| CAD Agent 规则/函数 | 解决的问题 | DWG 依赖 | DXF 对应证据 | 处理决定 |
|---|---|---|---|---|

For every relevant facts, text, geometry, candidate, hole, profile, solver,
quality, combined-output, primitive, and LISP rule, classify the decision as
`保留`、`重写`、`软证据` or `禁止迁移`.

- [ ] **Step 6: Record bounded claims**

The final report separately states:

- proven drawing families;
- calibration and acceptance pair counts;
- metamorphic invariants passed;
- unknown/uncovered topology;
- fail-closed behavior;
- remaining risks;
- whether the supervised gate is still draft or explicitly approved.
