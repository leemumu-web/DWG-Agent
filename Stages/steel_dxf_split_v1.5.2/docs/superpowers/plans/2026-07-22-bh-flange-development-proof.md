# BH Flange Development Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将两个 BH 翼缘展开复核案提升为由来源拓扑和已批准 Tekla profile 共同授权的生产结果，并完成 BH、BOX、统一 Worker 与 Linux 发布门禁。

**Architecture:** 在现有 `BHKnowledgeBase` 内声明单一 profile 级展开策略；几何层保留原始路径并生成证书，语义和关键证明层消费证书决定授权。人工拆板继续只在结果冻结后离线比较，不增加第二套算法或路由。

**Tech Stack:** Python 3.11、ezdxf、Shapely、pytest、Ruff、现有 BH ManufacturingIR/证明框架。

## Global Constraints

- BH 基线为 v1.5.2；本改动必须作为有声明的制造语义补丁进入来源审计。
- 只允许 `project_tekla_bh_dxf_v1` 使用该策略；不得按样本名、哈希、固定坐标或特定长度分支。
- 直接投影长度保持源精度；经证明的推导长度以 1 mm 为量子向下取整。
- 折线路径必须由显示精度约束唯一绑定；禁止无条件最近值回退。
- 人工参考不得进入编译、求解、证明或生产路由。
- BOX 内核与统一 family dispatcher 不改变。
- 不安装依赖、不启动 Docker、不 commit/push；Linux 环境动作遵守单独授权边界。

---

### Task 1: 固定新行为与失败关闭测试

**Files:**
- Create: `tests/bh_v152/test_bh_flange_development_policy.py`
- Modify: `tests/bh_v152/test_bh_information_usage.py`
- Modify: `tests/bh_v152/test_bh_negative_semantics.py`

**Interfaces:**
- Consumes: `BHCompiler`, `BHKnowledgeBase`, `DEFAULT_TEKLA_BH_SOURCE_CONTRACT`。
- Produces: 两个真实正例和禁用策略、非唯一候选、无匹配候选的失败关闭合同。

- [ ] **Step 1: 写真实正例失败测试**

```python
@pytest.mark.parametrize(
    ("stem", "expected_lengths", "expected_quantities"),
    [
        ("2b1-cb-40", [2538.0, 2383.037], [1, 1]),
        ("2b2-cb-10", [11294.0], [2]),
    ],
)
def test_profile_authorized_development_is_production_ready(
    stem: str,
    expected_lengths: list[float],
    expected_quantities: list[int],
) -> None:
    compiled = _compile(stem)
    assert compiled.assessment.disposition.value == "auto_accept"
    assert [plate.bbox.width for plate in compiled.assembly.flange_plates] == pytest.approx(
        expected_lengths, abs=0.01
    )
    assert [plate.quantity for plate in compiled.assembly.flange_plates] == expected_quantities
```

- [ ] **Step 2: 运行正例并确认红灯**

Run: `.venv\Scripts\python.exe -m pytest tests\bh_v152\test_bh_flange_development_policy.py -q`

Expected: 两个样本仍为 `review_required`，且长度仍是未量化值。

- [ ] **Step 3: 写三类失败关闭测试**

```python
def test_disabled_profile_policy_keeps_derived_development_in_review() -> None:
    knowledge = replace(
        DEFAULT_BH_KNOWLEDGE,
        flange_development_policy=replace(
            DEFAULT_BH_KNOWLEDGE.flange_development_policy,
            enabled=False,
        ),
    )
    assert _compile("2b1-cb-40", knowledge=knowledge).assessment.disposition.value == "review_required"


@pytest.mark.parametrize(
    ("candidates", "expected_matches"),
    [((11294.9, 11295.1), 2), ((11290.0, 11300.0), 0)],
)
def test_cranked_candidate_requires_one_display_precision_match(
    candidates: tuple[float, ...], expected_matches: int
) -> None:
    result = select_profile_authorized_cranked_candidate(
        candidates,
        nominal_length_mm=11295.0,
        nominal_text="11295",
        policy=DEFAULT_BH_KNOWLEDGE.flange_development_policy,
        geometric_tolerance_mm=0.15,
    )
    assert result.match_count == expected_matches
    assert result.authorized is False
```

- [ ] **Step 4: 运行负例并确认缺少接口或错误授权**

Run: `.venv\Scripts\python.exe -m pytest tests\bh_v152\test_bh_flange_development_policy.py -q`

Expected: 因策略/选择接口尚不存在而失败。

---

### Task 2: 建立 profile 策略和纯函数候选绑定

**Files:**
- Modify: `src/steel_dxf_split/bh_knowledge.py`
- Modify: `src/steel_dxf_split/bh_development.py`
- Test: `tests/bh_v152/test_bh_flange_development_policy.py`

**Interfaces:**
- Produces: `BHFlangeDevelopmentPolicy`、`CrankedCandidateSelection`、`quantize_derived_flange_length()`、`select_profile_authorized_cranked_candidate()`。
- Consumes: `displayed_dimension_tolerance()` 与现有制造容差。

- [ ] **Step 1: 增加冻结策略类型**

```python
@dataclass(frozen=True, slots=True)
class BHFlangeDevelopmentPolicy:
    enabled: bool = True
    profile_id: str = "project_tekla_bh_dxf_v1"
    derived_length_quantum_mm: float = 1.0
    derived_length_rounding: str = "floor"
    preserve_direct_projection: bool = True
    require_unique_cranked_candidate: bool = True


class BHKnowledgeBase:
    flange_development_policy: BHFlangeDevelopmentPolicy = BHFlangeDevelopmentPolicy()
```

- [ ] **Step 2: 增加纯函数量化和唯一候选选择**

```python
@dataclass(frozen=True, slots=True)
class CrankedCandidateSelection:
    authorized: bool
    selected_raw_length_mm: float | None
    quantized_length_mm: float | None
    candidate_count: int
    match_count: int
    tolerance_mm: float


def quantize_derived_flange_length(value: float, policy: BHFlangeDevelopmentPolicy) -> float:
    quantum = policy.derived_length_quantum_mm
    if not policy.enabled or policy.derived_length_rounding != "floor" or quantum <= 0.0:
        raise ValueError("BH flange development policy is not authorized")
    return floor(value / quantum + 1e-9) * quantum


def select_profile_authorized_cranked_candidate(
    candidates: Iterable[float],
    *,
    nominal_length_mm: float,
    nominal_text: str,
    policy: BHFlangeDevelopmentPolicy,
    geometric_tolerance_mm: float,
) -> CrankedCandidateSelection:
    tolerance = displayed_dimension_tolerance(
        nominal_text,
        geometric_tolerance_mm=geometric_tolerance_mm,
    )
    matches = tuple(value for value in candidates if abs(value - nominal_length_mm) <= tolerance)
    authorized = policy.enabled and len(matches) == 1
    selected = matches[0] if authorized else None
    return CrankedCandidateSelection(
        authorized,
        selected,
        quantize_derived_flange_length(selected, policy) if selected is not None else None,
        len(candidates),
        len(matches),
        tolerance,
    )
```

- [ ] **Step 3: 运行纯函数测试**

Run: `.venv\Scripts\python.exe -m pytest tests\bh_v152\test_bh_flange_development_policy.py -q`

Expected: 纯函数测试通过，真实 DXF 正例仍失败。

---

### Task 3: 将来源拓扑证书接入几何降低

**Files:**
- Modify: `src/steel_dxf_split/bh_geometry.py`
- Modify: `src/steel_dxf_split/bh_extractor.py`
- Modify: `src/steel_dxf_split/bh_solver.py`
- Test: `tests/bh_v152/test_bh_flange_development_policy.py`
- Test: `tests/bh_v152/test_bh_engineering_regressions.py`

**Interfaces:**
- Consumes: Task 2 的策略和纯函数。
- Produces: `FlangeDevelopmentEstimate.certificate`，以及诊断中的 `raw_lengths_mm`、`target_lengths_mm`、`candidate_count`、`match_count`、`quantization_policy`。

- [ ] **Step 1: 扩展估计结果而不丢失原始观察**

```python
@dataclass(frozen=True, slots=True)
class FlangeDevelopmentEstimate:
    mode: str
    target_lengths: tuple[float, ...]
    raw_lengths: tuple[float, ...]
    source_projection_length: float
    details: tuple[dict[str, object], ...]
    certificate: dict[str, object]
```

- [ ] **Step 2: 对直条路径执行证书守卫后量化**

```python
valid = (
    detail["method"] == "straight_strip_projection"
    and bool(detail["straight"])
    and abs(float(detail["observed_strip_thickness_mm"]) - flange_thickness) <= strip_tolerance
    and float(detail["rectangular_fill_ratio"]) >= 0.98
)
target = (
    source_projection_length
    if abs(raw - source_projection_length) <= manufacturing_tolerance_mm
    else quantize_derived_flange_length(raw, development_policy)
    if valid and development_policy.enabled
    else raw
)
```

- [ ] **Step 3: 对折线路径只接受唯一显示精度匹配**

```python
selection = select_profile_authorized_cranked_candidate(
    path_candidates,
    nominal_length_mm=nominal_length,
    nominal_text=f"{nominal_length:g}",
    policy=development_policy,
    geometric_tolerance_mm=manufacturing_tolerance_mm,
)
target = selection.quantized_length_mm if selection.authorized else min(path_candidates)
```

证书必须显式记录 `authorized=False`；此时目标值仅用于复核候选，不能授权生产。

- [ ] **Step 4: 从 solver 传递知识策略并写入 diagnostics**

Run: `.venv\Scripts\python.exe -m pytest tests\bh_v152\test_bh_flange_development_policy.py tests\bh_v152\test_bh_engineering_regressions.py -q`

Expected: 两张几何长度正确；处置仍可能因证明层未接入而失败。

---

### Task 4: 让关键证明消费 profile 证书

**Files:**
- Modify: `src/steel_dxf_split/bh_development.py`
- Modify: `src/steel_dxf_split/bh_constraints.py`
- Modify: `tests/bh_v152/test_bh_information_usage.py`
- Modify: `tests/bh_v152/test_bh_negative_semantics.py`
- Test: `tests/bh_v152/test_bh_flange_development_policy.py`

**Interfaces:**
- Consumes: `development["certificate"]`。
- Produces: `profile_authorized_rigid_development` 或 `profile_authorized_cranked_development` 证据通道，以及 `BH.PROOF.FLANGE.DEVELOPMENT=PASS`。

- [ ] **Step 1: 语义评估输出授权状态**

```python
certificate = development.get("certificate", {}) or {}
profile_authorized = bool(certificate.get("authorized"))
fabrication_authority = (
    "profile_authorized_source_geometry"
    if profile_authorized
    else "bound_total_length_required"
)
```

- [ ] **Step 2: 证明优先接受完整 profile 证书，显式总尺寸仍作为兼容授权**

```python
dimension_complete = (
    required_development_count > 0
    and covered_development_count == required_development_count
)
development_status = (
    ProofStatus.PASS
    if bool(development_assessment.get("profile_authorized")) or dimension_complete
    else ProofStatus.MISSING
)
```

- [ ] **Step 3: 保留失败诊断并增加证据字段断言**

Run: `.venv\Scripts\python.exe -m pytest tests\bh_v152\test_bh_flange_development_policy.py tests\bh_v152\test_bh_information_usage.py tests\bh_v152\test_bh_negative_semantics.py -q`

Expected: 真实两图 `auto_accept`；合成无证书负例仍 `review_required`。

---

### Task 5: 更新 BH 语料合同和离线监督门禁

**Files:**
- Modify: `tests/fixtures/bh_corpus.json`
- Modify: `tests/bh_v152/test_bh_corpus_regression.py`
- Modify: `tests/bh_v152/test_version_contract.py`
- Modify: `tests/bh_v152/test_bh_release_verifier.py`
- Modify: `src/steel_dxf_split/release_evidence/project_tekla_bh_dxf_v1.json`
- Modify: `docs/bh/VALIDATION.md`
- Modify: `docs/bh/REVIEW_WORKFLOW.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: 20 张真实编译与离线人工比较结果。
- Produces: `20/0/0` 语料、发布和文档合同。

- [ ] **Step 1: 将两图预期长度和处置改为生产**

`2b1-cb-40` 使用 `2538.0` 与 `2383.037`；`2b2-cb-10` 使用 `11294.0`、数量 2；两项
`blocking_proof_ids` 置空且 `disposition` 为 `auto_accept`。

- [ ] **Step 2: 运行真实 20 图回归**

Run: `.venv\Scripts\python.exe -m pytest tests\bh_v152\test_bh_corpus_regression.py -q`

Expected: 20 个参数化 case 全部生产路由且离线比较通过。

- [ ] **Step 3: 更新发布计数测试和文档，不手改无法由验证器证明的哈希**

Run: `.venv\Scripts\python.exe -m pytest tests\bh_v152\test_version_contract.py tests\bh_v152\test_bh_release_verifier.py -q`

Expected: 新的 `20/0/0` 合同通过；旧固定 evidence 因知识指纹变化明确失效，Task 6
使用候选模式生成并固定新 evidence 后再走正常验证。

---

### Task 6: 回归、来源审计和统一 Worker 验收

**Files:**
- Modify: `src/steel_dxf_split/release_evidence/project_tekla_bh_dxf_v1.json`
- Modify: `src/steel_dxf_split/bh_release_evidence.py` 中固定的新 evidence SHA-256。
- Verify only: v1.5.2 上游源码与当前 `src/steel_dxf_split` 的逐文件 SHA-256 对照；不为审计另建运行时抽象。
- No production changes outside discovered failures.

**Interfaces:**
- Consumes: Tasks 1-5 的完整实现。
- Produces: Windows 完整验证证据和可供 Linux 门禁复验的发布候选。

- [ ] **Step 1: 运行 BH 针对性与完整当前套件**

Run: 逐文件隔离执行 `tests\bh_v152\test_*.py`，避免 Windows 长进程内存累积。

Expected: 0 failed；平台限定项只允许有明确理由的 skip。

- [ ] **Step 2: 运行 160 次表示不变性**

Run: `.venv\Scripts\python.exe -m pytest tests\bh_v152\test_bh_representation_invariance.py -q`

Expected: 160 passed。

- [ ] **Step 3: 重跑 BOX 权威 20 张和项目 2 的 30 张**

Run: 使用现有统一 `split_dxf`/BOX 验证脚本，输入目录只读，并在运行前后比较源集 SHA-256。

Expected: BOX 50/50 `auto_accept`，制造几何与先前权威结果不变。

- [ ] **Step 4: 重跑 Windows 70 张统一 Worker E2E**

Expected: BH 20、BOX 权威 20、项目 2 BOX 30 全部完成；BH 为 20/20 production，BOX 为 50/50 auto accepted；保存后 DXF audit、报告、预览和重复字节确定性通过。

- [ ] **Step 5: 静态和仓库完整性检查**

Run: Ruff、format check、`compileall -f`、`git diff --check`、BH/BOX 来源审计和 `pytest --collect-only`。

Expected: 当前测试清单零收集错误；若仅有已确认的历史测试库存问题，必须在最终完成前显式处理，不能忽略。

- [ ] **Step 6: Linux 原生最终发布门禁**

Run: 在已授权 Linux 环境执行冻结依赖检查、完整 pytest、POSIX 进程树超时、20 图 BH 批处理、BH release verification、BOX release verification 和统一 batch CLI。

Expected: 所有 Linux 专属测试通过；正式 BH/BOX 证明绑定当前实现；无测试用 attestation；发布候选满足最终 Worker 合同。
