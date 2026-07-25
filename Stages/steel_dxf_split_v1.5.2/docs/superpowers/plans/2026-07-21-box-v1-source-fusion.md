# BOX v1.0.0 源码级融合实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `box-dxf-split v1.0.0` 完整迁入主项目，形成与 BH 对称的单一 BOX 编译器，并彻底移除外部依赖、双后端、旧求解器和 `SplitAssembly` 适配桥。

**Architecture:** 项目 2 的源码、制造 IR、证明、writer 和 saved-DXF validator 保持算法权威；主项目只在它的制造 IR 之后提供 release attestation、全批次 staging、原子提升和统一 BH/BOX 路由。BOX 生产调用图固定为 `Frontend -> Analysis -> Solve -> Manufacturing -> Validation -> Delivery`，不存在 legacy fallback 或结果拼接。

**Tech Stack:** Python 3.12、ezdxf 1.4.x、Shapely 2.1.x、matplotlib 3.x、Pillow 10/11、pytest 8/9、PowerShell、Git。

## Global Constraints

- 项目 2 冻结基线必须是 tag `v1.0.0`、commit `5a2be1a82eb7235bcff62d97a13d2937f9ad026b`。
- 项目 2 源路径固定为 `D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0`；它只作为迁移来源和差分验证对象，不作为生产运行时依赖。
- `D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf` 与 `D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf` 必须只读，算法冲突与最终验收以这 20 对 DXF 为权威。
- 项目 2 已有角色、方向、孔归属、等价合并、几何重建、制造 IR 和证明语义不得被旧 BOX 重新解释。
- v1.0.0 的正式制造实体是闭合 `LWPOLYLINE`、原生 `CIRCLE` 和 `TEXT`；不得恢复 REGION/ACIS 生产输出。
- 自动正式输出必须同时具备单图 `auto_accept`、saved-DXF 验证通过和当前实现有效的 release attestation。
- 不读取金样做运行时决策；金样只允许在离线验收阶段、且在源图编译结果冻结后读取。
- 不 commit、不 push、不改写 Git 历史，除非用户另行明确授权。
- 当前任务内联执行，不派发子代理。

---

### Task 1: 冻结并迁入项目 2 v1.0.0 源码

**Files:**

- Create: `src/steel_dxf_split/box/__init__.py`
- Create: `src/steel_dxf_split/box/artifact_io.py`
- Create: `src/steel_dxf_split/box/assembly.py`
- Create: `src/steel_dxf_split/box/batch_cli.py`
- Create: `src/steel_dxf_split/box/box_region.py`
- Create: `src/steel_dxf_split/box/cli.py`
- Create: `src/steel_dxf_split/box/course_graph.py`
- Create: `src/steel_dxf_split/box/dxf_artifact_io.py`
- Create: `src/steel_dxf_split/box/dxf_io.py`
- Create: `src/steel_dxf_split/box/equivalence.py`
- Create: `src/steel_dxf_split/box/flange_solver.py`
- Create: `src/steel_dxf_split/box/inspect_cli.py`
- Create: `src/steel_dxf_split/box/manufacturing_ir.py`
- Create: `src/steel_dxf_split/box/metadata.py`
- Create: `src/steel_dxf_split/box/openings.py`
- Create: `src/steel_dxf_split/box/pipeline.py`
- Create: `src/steel_dxf_split/box/preview.py`
- Create: `src/steel_dxf_split/box/process_control.py`
- Create: `src/steel_dxf_split/box/projection_geometry.py`
- Create: `src/steel_dxf_split/box/projection_lowering.py`
- Create: `src/steel_dxf_split/box/proofs.py`
- Create: `src/steel_dxf_split/box/source_ir.py`
- Create: `src/steel_dxf_split/box/validator.py`
- Create: `src/steel_dxf_split/box/view_frame.py`
- Create: `src/steel_dxf_split/box/view_solver.py`
- Create: `src/steel_dxf_split/box/web_solver.py`
- Create: `src/steel_dxf_split/box/weld_allowance.py`
- Create: `src/steel_dxf_split/box/weld_allowance_cli.py`
- Create: `src/steel_dxf_split/box/weld_allowance_release.py`
- Create: `src/steel_dxf_split/box/writer.py`
- Create: `src/steel_dxf_split/box/provenance.py`
- Create: `scripts/verify_box_v1_source.py`
- Create: `tests/box_v1/__init__.py`
- Create: `tests/box_v1/test_source_provenance.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `docs/superpowers/specs/2026-07-20-box-true-source-fusion-design.md`

**Interfaces:**

- Consumes: upstream source tree at exact commit `5a2be1a82eb7235bcff62d97a13d2937f9ad026b`.
- Produces: importable internal package `steel_dxf_split.box` and constants `BOX_CORE_VERSION`, `BOX_CORE_TAG`, `BOX_CORE_COMMIT`.

- [x] **Step 1: 写入先失败的源码归属测试**

```python
from pathlib import Path

from steel_dxf_split.box.provenance import (
    BOX_CORE_COMMIT,
    BOX_CORE_TAG,
    BOX_CORE_VERSION,
)


def test_internal_box_core_is_exact_v1_release() -> None:
    package = Path(__file__).parents[2] / "src/steel_dxf_split/box"
    assert package.is_dir()
    assert BOX_CORE_VERSION == "1.0.0"
    assert BOX_CORE_TAG == "v1.0.0"
    assert BOX_CORE_COMMIT == "5a2be1a82eb7235bcff62d97a13d2937f9ad026b"


def test_runtime_has_no_external_box_distribution_import() -> None:
    package = Path(__file__).parents[2] / "src/steel_dxf_split/box"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in package.rglob("*.py")
    )
    assert "from box_dxf_split" not in source
    assert "import box_dxf_split" not in source
```

- [x] **Step 2: 运行测试并确认缺少内部包**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest -q -p no:cacheprovider tests/box_v1/test_source_provenance.py
```

Expected: FAIL，提示 `steel_dxf_split.box` 或 `provenance` 不存在。

- [x] **Step 3: 机械迁入 30 个 v1.0.0 源文件**

将 `D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0\src\box_dxf_split\*.py`
逐字节复制到 `src\steel_dxf_split\box\`。复制后不得修改上述 30 个文件；集成代码使用新增模块承载。

`src/steel_dxf_split/box/provenance.py` 内容固定为：

```python
from __future__ import annotations

BOX_CORE_VERSION = "1.0.0"
BOX_CORE_TAG = "v1.0.0"
BOX_CORE_COMMIT = "5a2be1a82eb7235bcff62d97a13d2937f9ad026b"
BOX_CORE_SOURCE = "https://github.com/Creeken-Harrans/box-dxf-split"
```

- [x] **Step 4: 建立逐文件 SHA-256 差分验证器**

`scripts/verify_box_v1_source.py` 接受 `--upstream`，以冻结的 30 个上游文件名为
白名单，比较上游 `src/box_dxf_split/*.py` 与内部
`src/steel_dxf_split/box/*.py`。新增集成模块单独记录，不参与上游逐字节比较：

```json
{
  "schema": "BOX-V1-SOURCE-IMPORT-1.0",
  "tag": "v1.0.0",
  "commit": "5a2be1a82eb7235bcff62d97a13d2937f9ad026b",
  "matched": 30,
  "missing": [],
  "changed": [],
  "integration_files": ["provenance.py"]
}
```

任何 `missing` 或 `changed` 都返回非零退出码；`integration_files` 只允许包含计划中
明确创建的主项目集成模块。

- [x] **Step 5: 删除外部 Git 依赖并提升运行依赖**

`pyproject.toml` 的运行依赖改为：

```toml
dependencies = [
  "ezdxf>=1.4.4,<2",
  "matplotlib>=3.9,<4",
  "pillow>=10,<12",
  "shapely>=2.1,<3",
]
```

执行 `uv lock --offline`；不得执行依赖安装。

- [x] **Step 6: 验证源码镜像和导入**

Run:

```powershell
python scripts/verify_box_v1_source.py --upstream 'D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0'
python -c "from steel_dxf_split.box import __version__; assert __version__ == '1.0.0'"
```

Expected: `matched=30`、无差异，导入成功。

---

### Task 2: 迁入项目 2 行为测试并建立样例路径适配

**Files:**

- Create: `tests/box_v1/paths.py`
- Create: `tests/box_v1/conftest.py`
- Create: `tests/box_v1/test_artifact_io.py`
- Create: `tests/box_v1/test_assembly.py`
- Create: `tests/box_v1/test_box_region.py`
- Create: `tests/box_v1/test_course_graph.py`
- Create: `tests/box_v1/test_dxf_io.py`
- Create: `tests/box_v1/test_equivalence.py`
- Create: `tests/box_v1/test_flange_solver.py`
- Create: `tests/box_v1/test_generalization_contract.py`
- Create: `tests/box_v1/test_ground_truth_firewall.py`
- Create: `tests/box_v1/test_manufacturing_ir.py`
- Create: `tests/box_v1/test_metadata.py`
- Create: `tests/box_v1/test_openings.py`
- Create: `tests/box_v1/test_pipeline.py`
- Create: `tests/box_v1/test_preview.py`
- Create: `tests/box_v1/test_projection_geometry.py`
- Create: `tests/box_v1/test_projection_lowering.py`
- Create: `tests/box_v1/test_proofs.py`
- Create: `tests/box_v1/test_source_ir.py`
- Create: `tests/box_v1/test_view_frame.py`
- Create: `tests/box_v1/test_view_solver.py`
- Create: `tests/box_v1/test_web_solver.py`
- Create: `tests/box_v1/test_weld_allowance.py`
- Create: `tests/box_v1/test_weld_allowance_cli.py`
- Create: `tests/box_v1/test_weld_allowance_contract.py`
- Create: `tests/box_v1/test_weld_allowance_release.py`
- Create: `tests/box_v1/test_writer.py`

**Interfaces:**

- Consumes: `steel_dxf_split.box` exact source mirror and the existing read-only main-project sample bundle.
- Produces: v1.0.0 domain regression suite runnable inside the main repository.

- [x] **Step 1: 建立唯一测试路径定义**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "samples/box_pairs/BOX_拆板前_dxf"
REFERENCES = ROOT / "samples/box_pairs/BOX_拆板后_dxf"
PROJECT_2_INPUTS = Path(r"D:\DevData\项目2_BOX_dxf")
```

- [x] **Step 2: 机械迁入 v1.0.0 测试**

从上游复制列出的 26 个测试文件，并只进行以下机械替换：

```text
box_dxf_split.                 -> steel_dxf_split.box.
from box_dxf_split import      -> from steel_dxf_split.box import
ROOT / "samples/inputs"        -> INPUTS
ROOT / "samples/manual_references" -> REFERENCES
```

每个需要样例的文件从 `tests.box_v1.paths` 导入 `INPUTS`、`REFERENCES` 或
`PROJECT_2_INPUTS`。删除上游独立发行包、console-script 和仓库健康脚本断言；
这些由主项目自己的契约测试覆盖。

- [x] **Step 3: 运行纯领域测试**

Run:

```powershell
python -m pytest -q -p no:cacheprovider tests/box_v1 `
  --ignore=tests/box_v1/test_pipeline.py
```

Expected: 项目 2 算法、制造 IR、writer、validator 和焊接余量测试全部通过。
Windows 下对上游明确依赖 POSIX 目录 `fsync` 或指定 Noto/思源字体的环境契约
使用条件 skip；Windows 原子交付与预览由主项目集成测试单独覆盖。

- [x] **Step 4: 验证不存在旧主项目类型**

新增断言扫描 `tests/box_v1` 与 `src/steel_dxf_split/box`，必须不存在：

```text
SplitAssembly
steel_dxf_split.models.PlateRole
box_v2_backend
box_v2_pipeline
```

---

### Task 3: 建立 BH 风格的 BOX 编译 passes

**Files:**

- Create: `src/steel_dxf_split/box/contracts.py`
- Create: `src/steel_dxf_split/box/frontend.py`
- Create: `src/steel_dxf_split/box/analysis.py`
- Create: `src/steel_dxf_split/box/solve.py`
- Create: `src/steel_dxf_split/box/manufacturing.py`
- Create: `src/steel_dxf_split/box/validation.py`
- Create: `src/steel_dxf_split/box/compiler.py`
- Create: `tests/box_v1/test_compiler_passes.py`

**Interfaces:**

- Consumes: `build_source_ir()`, `resolve_box_metadata()`, `solve_complete_box()` and `validate_manufacturing_ir()` from the exact v1.0.0 mirror.
- Produces: `compile_box_core(input_path, source_contract) -> BoxCoreCompilation`.

- [x] **Step 1: 写入 pass 顺序和结果冻结测试**

```python
def test_compile_box_core_preserves_v1_mir_and_proof() -> None:
    result = compile_box_core(SAMPLE, BoxSourceContract())
    assert result.source.path == SAMPLE.resolve()
    assert result.search.best.mir is result.manufacturing
    assert result.proof_report is result.search.best.proof_report
    assert result.validation["ok"] is True
    assert result.manufacturing.fingerprint == result.fingerprint
    assert result.proof_report.disposition.value == "auto_accept"
```

- [x] **Step 2: 实现零语义改写的 pass 外观**

```python
def run_frontend(path: Path) -> SourceDocumentIR:
    return build_source_ir(path)


def run_analysis(source: SourceDocumentIR) -> BoxMetadata:
    return resolve_box_metadata(source)


def run_solve(
    source: SourceDocumentIR,
    metadata: BoxMetadata,
) -> AssemblySearchResult:
    return solve_complete_box(source, metadata)


def freeze_manufacturing(search: AssemblySearchResult) -> BoxManufacturingIR:
    return search.best.mir


def run_validation(manufacturing: BoxManufacturingIR) -> dict[str, object]:
    report = validate_manufacturing_ir(manufacturing)
    if report.get("ok") is not True:
        raise ValueError("BOX manufacturing IR validation failed")
    return report
```

- [x] **Step 3: 实现不可变核心编译结果**

```python
@dataclass(frozen=True, slots=True)
class BoxCoreCompilation:
    source: SourceDocumentIR
    metadata: BoxMetadata
    search: AssemblySearchResult
    manufacturing: BoxManufacturingIR
    proof_report: ProofReport
    validation: dict[str, object]

    @property
    def fingerprint(self) -> str:
        return self.manufacturing.fingerprint
```

`compile_box_core()` 必须严格按五个 pass 调用，并在读取任何人工参考前完成。

- [x] **Step 4: 运行 compiler pass 测试**

Run:

```powershell
python -m pytest -q -p no:cacheprovider tests/box_v1/test_compiler_passes.py
```

Expected: PASS，样例为 `auto_accept`，MIR 指纹稳定。

---

### Task 4: 接入 release attestation 与原子 Delivery

**Files:**

- Create: `src/steel_dxf_split/box/release.py`
- Create: `src/steel_dxf_split/box/delivery.py`
- Modify: `src/steel_dxf_split/box/compiler.py`
- Create: `tests/box_v1/test_release.py`
- Create: `tests/box_v1/test_delivery.py`

**Interfaces:**

- Consumes: frozen `BoxCoreCompilation`.
- Produces: `compile_box(input_path, *, config) -> BoxCompilationResult`，直接写 v1.0.0 原生 DXF，不创建 `SplitAssembly`。

- [x] **Step 1: 写入 release 双层授权测试**

```python
def test_auto_accept_without_release_is_quarantined_for_review(tmp_path: Path) -> None:
    result = compile_box(
        SAMPLE,
        config=BoxCompileConfig(
            output_dir=tmp_path,
            source_contract=BoxSourceContract(),
        ),
    )
    assert result.production_path is None
    assert result.review_path is not None
    assert result.report["automation_route"] == "review_required"
    assert result.report["single_file_disposition"] == "auto_accept"


def test_require_auto_accept_without_release_leaves_zero_artifacts(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="release attestation"):
        compile_box(
            SAMPLE,
            config=BoxCompileConfig(
                output_dir=tmp_path,
                source_contract=BoxSourceContract(),
                require_auto_accept=True,
            ),
        )
    assert not list(tmp_path.rglob("*"))
```

- [x] **Step 2: 实现配置和结果契约**

```python
@dataclass(frozen=True, slots=True)
class BoxCompileConfig:
    output_dir: Path
    source_contract: BoxSourceContract
    report_path: Path | None = None
    require_auto_accept: bool = False
    release_attestation_path: Path | None = None


@dataclass(frozen=True, slots=True)
class BoxCompilationResult:
    production_path: Path | None
    review_path: Path | None
    report_path: Path
    report: dict[str, object]
    core: BoxCoreCompilation
```

- [x] **Step 3: 实现内部源码指纹与 release attestation**

`production_implementation_fingerprint()` 递归散列：

```text
src/steel_dxf_split/box/**/*.py
src/steel_dxf_split/pipeline.py
src/steel_dxf_split/profile_detection.py
pyproject.toml
uv.lock
```

payload 必须记录 `BOX_CORE_VERSION`、`BOX_CORE_TAG` 和 `BOX_CORE_COMMIT`。
旧 `0.2.1` attestation 必须因实现指纹变化自然失效。

- [x] **Step 4: 实现直接 MIR Delivery**

Delivery 规则固定为：

```python
if disposition == "rejected":
    raise ValueError("rejected BOX proof cannot generate an output DXF")
if disposition == "auto_accept" and release_attestation is not None:
    route = "auto_accepted"
    purpose = OutputPurpose.PRODUCTION
elif disposition == "auto_accept":
    route = "review_required"
    purpose = OutputPurpose.PRODUCTION  # 外层隔离为认证候选，绝不正式提升
else:
    route = "review_required"
    purpose = OutputPurpose.REVIEW
```

所有 DXF、预览、源图副本和报告先写入同盘 staging；调用 v1.0.0
`write_box_clean()` 与 `validate_saved_dxf()` 通过后，才原子提升到最终路径。
任何异常恢复旧产物并删除本次 staged 产物。

- [x] **Step 5: 固定报告契约**

报告 schema 为 `BOX-COMPILATION-REPORT-4.0`，至少包含：

```text
core.version/tag/commit
source_contract
single_file_disposition
automation_route
proof_report
search_status
manufacturing_ir.fingerprint
manufacturing_ir_validation
saved_dxf
release_attestation
ground_truth_used_for_decision=false
legacy_solver_called=false
writer=native_lwpolyline_circle
batch_atomicity
```

- [x] **Step 6: 运行 release 和 delivery 故障注入测试**

Run:

```powershell
python -m pytest -q -p no:cacheprovider `
  tests/box_v1/test_release.py `
  tests/box_v1/test_delivery.py
```

Expected: 缺 release 时隔离复核；有效 release 时正式输出；写出、验证或提升故障时零新正式产物并恢复旧产物。

---

### Task 5: 将统一主路由切换到唯一 BOX 编译器

**Files:**

- Modify: `src/steel_dxf_split/pipeline.py`
- Modify: `src/steel_dxf_split/cli.py`
- Modify: `src/steel_dxf_split/batch_cli.py`
- Modify: `src/steel_dxf_split/profile_detection.py`
- Modify: `tests/test_profile_detection_unique_v1.py`
- Create: `tests/test_box_single_core_route.py`
- Modify: `tests/test_box_atomic_pipeline_v1.py`
- Modify: `tests/test_box_gate_integration_v1.py`

**Interfaces:**

- Consumes: `BoxCompileConfig`, `BoxCompilationResult`, `BoxSourceContract`, `compile_box`.
- Produces: existing cross-family `split_dxf()` API with one BOX branch and unchanged BH branch.

- [x] **Step 1: 写入禁止双后端的契约测试**

```python
def test_split_options_has_no_box_backend() -> None:
    assert "box_backend" not in SplitOptions.__dataclass_fields__


def test_cli_has_no_box_backend_flag() -> None:
    actions = {option for action in build_parser()._actions for option in action.option_strings}
    assert "--box-backend" not in actions


def test_box_route_imports_only_internal_compiler() -> None:
    source = inspect.getsource(steel_dxf_split.pipeline)
    assert "box_v2_pipeline" not in source
    assert "box_pipeline" not in source
    assert "from .box.compiler import compile_box" in source
```

- [x] **Step 2: 简化 BOX 选项**

`SplitOptions` 保留 BH 现有字段，并将 BOX 字段固定为：

```python
box_source_contract: BoxSourceContract | None = None
box_release_attestation: Path | None = None
```

删除 `box_backend`。CLI 删除 `--box-backend`，保留
`--authorize-tekla-box-single-part-profile` 和 `--box-release-attestation`。

- [x] **Step 3: 实现唯一 BOX 路由**

```python
if family == "BOX":
    if options.box_source_contract is None:
        raise ValueError("BOX 需要显式 source contract。")
    compiled = compile_box(
        input_path,
        config=BoxCompileConfig(
            output_dir=output_dir,
            source_contract=options.box_source_contract,
            report_path=options.report_path,
            require_auto_accept=options.require_auto_accept,
            release_attestation_path=options.box_release_attestation,
        ),
    )
    return SplitResult(
        compiled.production_path,
        compiled.review_path,
        None,
        compiled.report_path,
        compiled.report,
    )
```

- [x] **Step 4: 更新单文件和批处理 CLI**

批处理子进程必须传播 source contract、release attestation、超时和
`require_auto_accept`；不得构造任何 backend 参数。每个 BOX 文件仍在独立进程中运行。

- [x] **Step 5: 运行路由与 BH 隔离测试**

Run:

```powershell
python -m pytest -q -p no:cacheprovider `
  tests/test_box_single_core_route.py `
  tests/test_profile_detection_unique_v1.py `
  tests/test_box_atomic_pipeline_v1.py `
  tests/test_box_gate_integration_v1.py `
  tests/test_bh_compiler_v08.py
```

Expected: BOX 只进入内部 compiler；混合 BH/BOX 证据拒绝；BH 路径行为不变。

---

### Task 6: 迁入离线金样比较器并覆盖权威 20 对

**Files:**

- Create: `tools/box_manual_reference.py`
- Create: `tools/compare_box_corpus.py`
- Create: `scripts/verify_box_v1_fusion.py`
- Create: `tests/box_v1/test_manual_reference.py`
- Create: `tests/box_v1/test_golden_corpus.py`
- Modify: `scripts/run_box_pairs.ps1`

**Interfaces:**

- Consumes: source-only `compile_box_core()` 冻结结果和只读拆板后 DXF。
- Produces: `BOX-V1-FUSION-ACCEPTANCE-1.0` JSON 验收报告。

- [x] **Step 1: 迁入 v1.0.0 curve-aware 人工参考解析器**

从上游 `tools/manual_reference.py` 和 `tools/compare_manual_corpus.py`
迁入，只修改 import namespace 和默认路径。生产包 `src/steel_dxf_split`
不得 import `tools`，比较器也不得在源图编译冻结前打开拆板后 DXF。

- [x] **Step 2: 写入金样只读与 ground-truth firewall 测试**

```python
def test_golden_comparison_freezes_source_result_before_reference_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(compare, "compile_source_only", lambda path: events.append("compile"))
    monkeypatch.setattr(compare, "load_manual_reference", lambda path: events.append("reference"))
    compare.compare_pair(INPUT, REFERENCE)
    assert events == ["compile", "reference"]
```

验收脚本必须在运行前后计算两目录每个文件的 SHA-256，并断言完全不变。

- [x] **Step 3: 实现 20 对验收**

每一对必须验证：

```text
proof disposition
完整四物理角色
输出板组数量与 quantity
外轮廓 Hausdorff/symmetric-difference/area
圆孔数量、中心与半径
零件号 member/family/quantity
MIR fingerprint
saved-DXF reopen validation
ground_truth_used_for_decision=false
```

- [x] **Step 4: 运行权威金样验收**

Run:

```powershell
python scripts/verify_box_v1_fusion.py `
  --inputs 'D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf' `
  --references 'D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf' `
  --output 'docs/superpowers/reports/2026-07-21-box-v1-fusion-acceptance.json'
```

Expected: 20/20 通过，两个权威目录哈希运行前后完全相同。

---

### Task 7: 删除旧 BOX 算法、适配器和错误方案产物

**Files:**

- Delete: `src/steel_dxf_split/box_compiler.py`
- Delete: `src/steel_dxf_split/box_facts.py`
- Delete: `src/steel_dxf_split/box_geometry_ir.py`
- Delete: `src/steel_dxf_split/box_geometry_roles.py`
- Delete: `src/steel_dxf_split/box_manufacturing.py`
- Delete: `src/steel_dxf_split/box_metadata.py`
- Delete: `src/steel_dxf_split/box_pipeline.py`
- Delete: `src/steel_dxf_split/box_projection.py`
- Delete: `src/steel_dxf_split/box_reconstruction.py`
- Delete: `src/steel_dxf_split/box_solver.py`
- Delete: `src/steel_dxf_split/box_text_evidence.py`
- Delete: `src/steel_dxf_split/box_validator.py`
- Delete: `src/steel_dxf_split/box_view_ir.py`
- Delete: `src/steel_dxf_split/box_writer.py`
- Delete: `src/steel_dxf_split/box_delivery_ir.py`
- Delete: `src/steel_dxf_split/box_delivery_validator.py`
- Delete: `src/steel_dxf_split/box_delivery_writer.py`
- Delete: `src/steel_dxf_split/box_region.py`
- Delete: `src/steel_dxf_split/box_release.py`
- Delete: `src/steel_dxf_split/box_v2_backend.py`
- Delete: `src/steel_dxf_split/box_v2_pipeline.py`
- Delete: `src/steel_dxf_split/box_supervision.py`
- Delete: `src/steel_dxf_split/box_supervision_cli.py`
- Delete: `scripts/box_corpus_audit.py`
- Delete: `scripts/verify_box_v2_fusion.py`
- Delete: legacy `tests/test_box_*.py` files superseded by `tests/box_v1/`
- Replace: `tests/test_box_architecture_v2.py` → `tests/test_box_architecture.py`
- Modify: `README.md`
- Modify: `CONTEXT.md`

**Interfaces:**

- Consumes: fully passing internal BOX v1 compiler, delivery and golden tests.
- Produces: one BOX implementation in the release package.

- [x] **Step 1: 写入生产调用图负面测试**

```python
FORBIDDEN = {
    "box_v2_backend",
    "box_v2_pipeline",
    "box_compiler",
    "box_solver",
    "box_reconstruction",
    "SplitAssembly",
}


def test_production_box_call_graph_contains_no_legacy_nodes() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in PRODUCTION_BOX_FILES
    )
    assert not {token for token in FORBIDDEN if token in sources}
```

- [x] **Step 2: 删除旧实现和对应测试**

只有在 Tasks 1–6 全部通过后执行删除。保留跨型号通用模块、BH 模块和
`models.py`；不得顺手重构无关代码。

- [x] **Step 3: 更新架构文档**

README 和 CONTEXT 必须明确：

```text
BOX core: internal steel_dxf_split.box, source baseline v1.0.0
commit: 5a2be1a82eb7235bcff62d97a13d2937f9ad026b
runtime external dependency: none
legacy fallback: none
production entities: native LWPOLYLINE/CIRCLE/TEXT
```

旧 `2026-07-20-box-v2-core-fusion-design.md` 和计划只能标注为废弃历史，
不得被 README、CONTEXT 或当前计划引用为实施依据。

- [x] **Step 4: 运行结构约束测试**

Run:

```powershell
python -m pytest -q -p no:cacheprovider `
  tests/test_box_architecture.py `
  tests/test_box_single_core_route.py `
  tests/box_v1/test_source_provenance.py
```

Expected: 无旧模块、无外部 distribution import、无双后端、无 `SplitAssembly` 桥。

---

### Task 8: 全量回归、静态检查与交付报告

**Files:**

- Create: `docs/superpowers/reports/2026-07-21-box-v1-source-fusion-validation.md`
- Modify: `docs/superpowers/plans/2026-07-21-box-v1-source-fusion.md`

**Interfaces:**

- Consumes: Tasks 1–7 完成后的单一实现。
- Produces: 可复核的测试、金样、静态检查和剩余风险结论。

- [x] **Step 1: 运行 BOX v1 全套**

```powershell
python -m pytest -q -p no:cacheprovider tests/box_v1
```

Expected: 全部通过，只有上游明确保留的条件 skip。

- [x] **Step 2: 运行主项目全套**

```powershell
python -m pytest -q -p no:cacheprovider tests
```

Expected: 全部通过；BH 回归无变化。

- [x] **Step 3: 运行静态检查**

```powershell
ruff check src tests scripts tools
mypy src
```

Expected: 零错误。为保持 30 个上游文件逐字节一致，在 `pyproject.toml` 中仅对以下
两处冻结源码配置精确 per-file ignore，不直接修改源码：

```toml
[tool.ruff.lint.per-file-ignores]
"src/steel_dxf_split/box/manufacturing_ir.py" = ["UP038"]
"src/steel_dxf_split/box/weld_allowance_cli.py" = ["UP038"]
```

实际结果：本次 BOX/入口/脚本/工具范围 Ruff 和 18 个集成源文件 Mypy 通过；
全仓 Ruff 仍有 8 条未修改 BH/旧脚本的既有 F401，全仓 Mypy 仍有 21 个文件
117 条既有类型债务。没有修改 30 个冻结源码或顺手重构旧 BH 来掩盖基线。

- [x] **Step 4: 重跑源码与金样验证**

```powershell
python scripts/verify_box_v1_source.py --upstream 'D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0'
python scripts/verify_box_v1_fusion.py `
  --inputs 'D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf' `
  --references 'D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf'
```

Expected: 30 个上游文件逐字节无差异；权威样例 20/20；输入和参考目录哈希不变。

- [x] **Step 5: 写入最终验证报告**

报告必须包含：

```text
本地 branch/HEAD
项目 2 tag/commit
迁入文件清单与差异
BOX v1 测试数量
主项目测试数量
BH 回归结果
20 对金样结果
writer 实体统计
release attestation 状态
ruff/mypy 结果
未运行验证及原因
剩余风险
```

不得在没有对应命令输出时宣称通过。
