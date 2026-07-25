# BOX 项目 2 单内核压缩融合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以项目 2 v1.0.0 为唯一 BOX 内核，把旧算法尚未覆盖的输入资源预算压缩吸收到统一前端，并证明其余安全能力已经由项目 2 契约覆盖。

**Architecture:** 保持项目 2 的 30 个冻结核心文件不变。在主项目集成层 `contracts.py` 定义资源预算，`frontend.py` 对不可变 `SourceDocumentIR` 做确定性检查，然后才进入项目 2 analysis/solve。没有旧 solver、fallback 或第二份结果；其余旧能力只通过项目 2 的现有行为测试确认。

**Tech Stack:** Python 3.12、ezdxf 1.4、pytest 9、Ruff、Mypy、PowerShell。

## Global Constraints

- 项目 2 基线固定为 tag `v1.0.0`、commit `5a2be1a82eb7235bcff62d97a13d2937f9ad026b`。
- `D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf`、`D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf` 和 `D:\DevData\项目2_BOX_dxf` 只读。
- 不恢复旧 BOX 顶层算法模块，不添加双后端、fallback、投票、拼接或 `SplitAssembly` 降级链。
- 先看测试按预期失败，再写最小生产实现。
- 本计划不覆盖 BH v1.5.1，不处理无关全仓类型债务。
- 未获用户授权，不 commit、push 或合并；以 Git diff 检查点代替提交步骤。

---

## File Structure

- Modify: `src/steel_dxf_split/box/contracts.py` — 定义不可变 `BoxSourceLimits`。
- Modify: `src/steel_dxf_split/box/frontend.py` — 检查 SourceIR 资源预算并提供稳定失败码。
- Modify: `src/steel_dxf_split/box/compiler.py` — 把预算贯穿唯一编译入口。
- Create: `tests/test_box_compressed_capabilities.py` — 覆盖新增预算和入口传播。
- Modify: `tests/box_v1/test_compiler_passes.py` — 让 pass-order 监视器透明转发新增关键字参数。
- Create: `docs/superpowers/reports/2026-07-21-box-project2-core-compressed-fusion-validation.md` — 记录实测证据。

### Task 1: 定义资源预算契约

**Files:**
- Modify: `src/steel_dxf_split/box/contracts.py`
- Create: `tests/test_box_compressed_capabilities.py`

**Interfaces:**
- Produces: `BoxSourceLimits(max_entities, max_text_entities, max_points_per_entity, max_block_depth, max_abs_coordinate)`。
- Produces: `BoxSourceLimits.to_dict() -> dict[str, int | float]`。

- [ ] **Step 1: 写失败测试**

```python
from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf

import pytest

from steel_dxf_split.box.contracts import BoxSourceLimits


def test_compressed_source_limits_are_frozen_and_serializable() -> None:
    limits = BoxSourceLimits()
    assert limits.to_dict() == {
        "max_entities": 200_000,
        "max_text_entities": 50_000,
        "max_points_per_entity": 20_000,
        "max_block_depth": 16,
        "max_abs_coordinate": 1.0e9,
    }
    with pytest.raises(FrozenInstanceError):
        limits.max_entities = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("max_entities", 0),
        ("max_text_entities", 0),
        ("max_points_per_entity", 0),
        ("max_block_depth", 0),
        ("max_abs_coordinate", 0.0),
        ("max_abs_coordinate", inf),
    ),
)
def test_compressed_source_limits_reject_invalid_values(
    name: str,
    value: int | float,
) -> None:
    with pytest.raises(ValueError, match=name):
        BoxSourceLimits(**{name: value})  # type: ignore[arg-type]
```

- [ ] **Step 2: 运行并确认 RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_box_compressed_capabilities.py -q
```

Expected: collection fails because `BoxSourceLimits` is absent.

- [ ] **Step 3: 写最小实现**

Add to `contracts.py`:

```python
from math import isfinite


@dataclass(frozen=True, slots=True)
class BoxSourceLimits:
    max_entities: int = 200_000
    max_text_entities: int = 50_000
    max_points_per_entity: int = 20_000
    max_block_depth: int = 16
    max_abs_coordinate: float = 1.0e9

    def __post_init__(self) -> None:
        for name in (
            "max_entities",
            "max_text_entities",
            "max_points_per_entity",
            "max_block_depth",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not isfinite(self.max_abs_coordinate) or self.max_abs_coordinate <= 0:
            raise ValueError("max_abs_coordinate must be positive and finite")

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)
```

- [ ] **Step 4: 运行并确认 GREEN**

Expected: Task 1 tests pass.

### Task 2: 在唯一前端实施失败关闭

**Files:**
- Modify: `src/steel_dxf_split/box/frontend.py`
- Modify: `tests/test_box_compressed_capabilities.py`

**Interfaces:**
- Consumes: `BoxSourceLimits`。
- Produces: `BoxSourceLimitError(reason_code, observed, limit)`。
- Produces: `run_frontend(path, *, limits=BoxSourceLimits()) -> SourceDocumentIR`。

- [ ] **Step 1: 追加实体、文字、点数、块深度和坐标五类失败测试**

```python
from pathlib import Path

import ezdxf

from steel_dxf_split.box.frontend import BoxSourceLimitError, run_frontend


def _save(document, path: Path) -> Path:
    document.saveas(path)
    return path


def _assert_limit(path: Path, limits: BoxSourceLimits, code: str) -> None:
    with pytest.raises(BoxSourceLimitError) as captured:
        run_frontend(path, limits=limits)
    assert captured.value.reason_code == code


def test_frontend_rejects_entity_budget(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_line((0, 0), (1, 0))
    document.modelspace().add_line((1, 0), (1, 1))
    _assert_limit(
        _save(document, tmp_path / "entities.dxf"),
        BoxSourceLimits(max_entities=1),
        "source_entity_limit_exceeded",
    )


def test_frontend_rejects_text_budget(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_text("A")
    document.modelspace().add_text("B")
    _assert_limit(
        _save(document, tmp_path / "texts.dxf"),
        BoxSourceLimits(max_text_entities=1),
        "source_text_limit_exceeded",
    )


def test_frontend_rejects_points_budget(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_lwpolyline(((0, 0), (1, 0), (1, 1)))
    _assert_limit(
        _save(document, tmp_path / "points.dxf"),
        BoxSourceLimits(max_points_per_entity=2),
        "source_points_limit_exceeded",
    )


def test_frontend_rejects_block_depth(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    inner = document.blocks.new("INNER")
    inner.add_line((0, 0), (1, 0))
    outer = document.blocks.new("OUTER")
    outer.add_blockref("INNER", (0, 0))
    document.modelspace().add_blockref("OUTER", (0, 0))
    _assert_limit(
        _save(document, tmp_path / "depth.dxf"),
        BoxSourceLimits(max_block_depth=1),
        "source_block_depth_limit_exceeded",
    )


def test_frontend_rejects_coordinate_budget(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_line((0, 0), (11, 0))
    _assert_limit(
        _save(document, tmp_path / "coordinate.dxf"),
        BoxSourceLimits(max_abs_coordinate=10.0),
        "source_coordinate_limit_exceeded",
    )
```

- [ ] **Step 2: 运行并确认 RED**

Expected: import/signature failure because the error and `limits` API are absent.

- [ ] **Step 3: 写最小前端实现**

Implement in `frontend.py`:

```python
from collections.abc import Iterator
from math import isfinite

from .contracts import BoxSourceLimits
from .source_ir import SourceDocumentIR, SourceEntityIR, build_source_ir


class BoxSourceLimitError(ValueError):
    def __init__(
        self,
        reason_code: str,
        *,
        observed: int | float,
        limit: int | float,
    ) -> None:
        self.reason_code = reason_code
        self.observed = observed
        self.limit = limit
        super().__init__(f"{reason_code}: observed={observed}, limit={limit}")


def _raise_if_over(
    reason_code: str,
    observed: int | float,
    limit: int | float,
) -> None:
    if observed > limit:
        raise BoxSourceLimitError(reason_code, observed=observed, limit=limit)


def _coordinate_values(entity: SourceEntityIR) -> Iterator[float]:
    for point in (entity.start, entity.end, entity.center, entity.major_axis):
        if point is not None:
            yield from point
    if entity.radius is not None:
        yield entity.radius
    for point in entity.points:
        yield from point


def _check_source_limits(source: SourceDocumentIR, limits: BoxSourceLimits) -> None:
    _raise_if_over(
        "source_entity_limit_exceeded", len(source.entities), limits.max_entities
    )
    _raise_if_over(
        "source_text_limit_exceeded",
        sum(entity.text_raw is not None for entity in source.entities),
        limits.max_text_entities,
    )
    for entity in source.entities:
        _raise_if_over(
            "source_points_limit_exceeded",
            len(entity.points),
            limits.max_points_per_entity,
        )
        _raise_if_over(
            "source_block_depth_limit_exceeded",
            max(0, len(entity.source_id.split("/")) - 1),
            limits.max_block_depth,
        )
        for value in _coordinate_values(entity):
            if not isfinite(value) or abs(value) > limits.max_abs_coordinate:
                raise BoxSourceLimitError(
                    "source_coordinate_limit_exceeded",
                    observed=value,
                    limit=limits.max_abs_coordinate,
                )


def run_frontend(
    path: str | Path,
    *,
    limits: BoxSourceLimits = BoxSourceLimits(),
) -> SourceDocumentIR:
    source = build_source_ir(path)
    _check_source_limits(source, limits)
    return source
```

- [ ] **Step 4: 运行并确认 GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_box_compressed_capabilities.py -q
```

Expected: all tests pass.

- [ ] **Step 5: 验证上游 SourceIR 仍冻结**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/box_v1/test_source_ir.py tests/box_v1/test_source_provenance.py -q
.\.venv\Scripts\python.exe scripts/verify_box_v1_source.py --upstream 'D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0'
```

Expected: tests pass and source verifier reports 30 exact files, 0 changed.

### Task 3: 贯穿唯一编译入口

**Files:**
- Modify: `src/steel_dxf_split/box/compiler.py`
- Modify: `tests/test_box_compressed_capabilities.py`
- Modify: `tests/box_v1/test_compiler_passes.py`

**Interfaces:**
- Adds: `BoxCompileConfig.source_limits: BoxSourceLimits`。
- Changes: `compile_box_core(input_path, source_contract, *, source_limits=BoxSourceLimits())`。

- [ ] **Step 1: 写入口传播失败测试**

```python
from steel_dxf_split.box.compiler import BoxCompileConfig, compile_box
from steel_dxf_split.box.contracts import BoxSourceContract


def test_compile_box_propagates_limits_to_the_only_frontend(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_line((0, 0), (1, 0))
    document.modelspace().add_line((1, 0), (1, 1))
    source = _save(document, tmp_path / "compiler-limit.dxf")
    config = BoxCompileConfig(
        output_dir=tmp_path / "output",
        source_contract=BoxSourceContract(),
        source_limits=BoxSourceLimits(max_entities=1),
    )
    with pytest.raises(BoxSourceLimitError) as captured:
        compile_box(source, config=config)
    assert captured.value.reason_code == "source_entity_limit_exceeded"
    assert not (tmp_path / "output").exists()
```

- [ ] **Step 2: 运行并确认 RED**

Expected: `BoxCompileConfig` rejects `source_limits`.

- [ ] **Step 3: 写最小传播实现**

Make these exact changes in `compiler.py`:

```python
from .contracts import BoxSourceContract, BoxSourceLimits


@dataclass(frozen=True, slots=True)
class BoxCompileConfig:
    output_dir: Path
    source_contract: BoxSourceContract
    report_path: Path | None = None
    require_auto_accept: bool = False
    release_attestation_path: Path | None = None
    source_limits: BoxSourceLimits = BoxSourceLimits()


def compile_box_core(
    input_path: str | Path,
    source_contract: BoxSourceContract,
    *,
    source_limits: BoxSourceLimits = BoxSourceLimits(),
) -> BoxCoreCompilation:
    source_contract.validate()
    source = run_frontend(input_path, limits=source_limits)
    metadata = run_analysis(source)
    search = run_solve(source, metadata)
    manufacturing = freeze_manufacturing(search)
    validation = run_validation(manufacturing)
    return BoxCoreCompilation(
        source=source,
        metadata=metadata,
        search=search,
        manufacturing=manufacturing,
        proof_report=search.best.proof_report,
        validation=validation,
    )
```

In `compile_box()`, replace the core call with:

```python
core = compile_box_core(
    input_path,
    config.source_contract,
    source_limits=config.source_limits,
)
```

Do not touch analysis、solve、manufacturing、writer or validator.

Update the existing pass-order recorder without weakening its assertions:

```python
def record(
    *args: object,
    _name: str = name,
    _original=original,
    **kwargs: object,
):
    events.append(_name)
    return _original(*args, **kwargs)
```

- [ ] **Step 4: 运行并确认 GREEN**

Run the compressed-capability test file. Expected: all tests pass and a budget failure creates no output directory.

- [ ] **Step 5: 运行入口与原子失败回归**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_box_single_core_route.py tests/test_box_atomic_pipeline_v1.py tests/box_v1/test_compiler_passes.py tests/box_v1/test_pipeline.py -q
```

Expected: all tests pass.

### Task 4: 验证压缩能力和只读语料

**Files:**
- Create: `docs/superpowers/reports/2026-07-21-box-project2-core-compressed-fusion-validation.md`

**Interfaces:**
- Confirms: single solver、proof fail-closed、hole ownership、provenance、ground-truth firewall、saved-DXF closure。
- Produces: one Chinese report containing only observed results.

- [ ] **Step 1: 运行安全契约与完整 BOX 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_box_architecture.py tests/test_box_single_core_route.py tests/box_v1/test_assembly.py tests/box_v1/test_proofs.py tests/box_v1/test_openings.py tests/box_v1/test_writer.py tests/box_v1/test_ground_truth_firewall.py -q
$boxIntegrationTests = Get-ChildItem tests -File -Filter 'test_box_*.py' | Select-Object -ExpandProperty FullName
& .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/box_v1 $boxIntegrationTests tests/test_profile_detection_unique_v1.py -q
```

Expected: no failures; upstream conditional skips may remain.

- [ ] **Step 2: 运行外部权威前后样例**

```powershell
.\.venv\Scripts\python.exe scripts/verify_box_v1_fusion.py --inputs 'D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf' --references 'D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf' --output "$env:TEMP\box-compressed-golden.json"
```

Expected: actual corpus count passes, saved DXFs reopen, and both directories remain hash-identical. The expected current count is 20, but the report must use the observed count.

- [ ] **Step 3: 运行项目 2 的 30 个独立 DXF**

```powershell
$script = @'
from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from steel_dxf_split.box.compiler import compile_box_core
from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.box.validator import validate_saved_dxf
from steel_dxf_split.box.writer import OutputPurpose, write_box_clean


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


root = Path(r"D:\DevData\项目2_BOX_dxf")
files = sorted(root.glob("*.dxf"))
before = {path.name: digest(path) for path in files}
results: list[dict[str, object]] = []
with TemporaryDirectory(prefix="box-project2-compressed-") as temporary:
    output_root = Path(temporary)
    for path in files:
        try:
            core = compile_box_core(path, BoxSourceContract())
            candidate = output_root / f"{path.stem}.dxf"
            layout = write_box_clean(
                core.manufacturing,
                candidate,
                purpose=OutputPurpose.PRODUCTION,
            )
            saved = validate_saved_dxf(
                candidate,
                core.manufacturing,
                layout=layout,
            )
            disposition = core.proof_report.disposition.value
            ok = (
                disposition == "auto_accept"
                and core.search.search_complete
                and saved.get("ok") is True
            )
            results.append(
                {
                    "file": path.name,
                    "disposition": disposition,
                    "saved_ok": saved.get("ok") is True,
                    "ok": ok,
                }
            )
        except Exception as error:
            results.append(
                {
                    "file": path.name,
                    "error": f"{type(error).__name__}: {error}",
                    "ok": False,
                }
            )
after = {path.name: digest(path) for path in files}
summary = {
    "input_count": len(files),
    "passed": sum(item["ok"] is True for item in results),
    "failed": sum(item["ok"] is not True for item in results),
    "dispositions": dict(
        Counter(
            str(item["disposition"])
            for item in results
            if "disposition" in item
        )
    ),
    "inputs_unchanged": before == after,
    "failures": [item for item in results if item["ok"] is not True],
}
print(json.dumps(summary, ensure_ascii=False))
if not (
    summary["input_count"] == 30
    and summary["passed"] == 30
    and summary["failed"] == 0
    and summary["inputs_unchanged"] is True
):
    raise SystemExit(1)
'@
$script | .\.venv\Scripts\python.exe -
```

Expected: observed current count 30, 30 pass, 0 fail, 30 `auto_accept`, inputs unchanged.

- [ ] **Step 4: 运行静态和来源检查**

```powershell
.\.venv\Scripts\python.exe scripts/verify_box_v1_source.py --upstream 'D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0'
.\.venv\Scripts\python.exe scripts/verify_box_v1_fusion.py --help
& 'D:\anaconda3\Scripts\ruff.exe' check src/steel_dxf_split/box/contracts.py src/steel_dxf_split/box/frontend.py src/steel_dxf_split/box/compiler.py tests/test_box_compressed_capabilities.py tests/box_v1/test_compiler_passes.py
& 'D:\anaconda3\Scripts\mypy.exe' --explicit-package-bases --follow-imports=skip --ignore-missing-imports src/steel_dxf_split/box/contracts.py src/steel_dxf_split/box/frontend.py src/steel_dxf_split/box/compiler.py tests/test_box_compressed_capabilities.py tests/box_v1/test_compiler_passes.py
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 5: 用 apply_patch 写最终报告并检查状态**

Report sections:

```markdown
# BOX 项目 2 单内核压缩融合验证报告
## 结论
## 实际修改
## 旧能力压缩映射
## RED-GREEN 证据
## 定向与完整测试
## 权威前后样例
## 项目 2 独立语料
## 单内核与来源证明
## 剩余风险和 BH 后续边界
```

Each old capability must be marked `项目 2 已覆盖`, `新增最小补丁`, or `拒绝/延期吸收`. Run:

```powershell
git status --short
git diff --check
```

Expected: only intended compressed-fusion files plus pre-existing worktree changes; no commit or push.
