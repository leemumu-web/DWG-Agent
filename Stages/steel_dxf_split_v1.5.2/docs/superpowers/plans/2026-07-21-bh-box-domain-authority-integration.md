# BH/BOX 领域权威集成实施计划

> 已废弃：用户否决了 `application/adapters` 复杂分层。当前实施以
> `2026-07-21-bh-v152-box-simple-worker-fusion.md` 为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 建成一个 DXF 拆板框架：公共入口自动判型，每张图只调用完整 BH v1.5.2
或当前融合后的 Project2 BOX v1.0.0 领域内核之一。

**架构：** 把完整 BH v1.5.2 放入 `steel_dxf_split.bh`，保留已验证 BOX 于
`steel_dxf_split.box`，判型、授权、固定 adapter 分派、统一结果和批次事务归入
`steel_dxf_split.application`。包根只提供 `split_dxf()` 与 `split_batch()`；
领域报告继续是权威，不引入共享几何模型。

**技术栈：** Python 3.12、ezdxf 1.4.4、Shapely 2.1、Pillow、matplotlib、
pytest 9、Ruff 0.14、PowerShell 7/Windows 11 开发环境、Linux 生产 Worker、Git。

## 全局约束

- 只在 `D:\Dev\Projects\dxf agent\worktrees\box-completion` 工作。
- BH 权威来源为 `D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2`，tag
  `v1.5.2`，commit `302dd73fa4b92f1d39486063c15dd49227e58b8a`。
- BOX 权威来源为 `D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0`，
  Project2 v1.0.0 commit
  `5a2be1a82eb7235bcff62d97a13d2937f9ad026b`；当前内核由 23 个逐字节一致文件和
  `box-notch-hotfix-2026-07-21` 中 7 个显式、双向 SHA-256 约束的补丁文件组成，
  不得把这些已审查差异重新宣称为 30/30 逐字节一致。
- 一个输入绝不同时运行两个内核；不允许 fallback、投票、结果拼接、backend 选择或
  ManufacturingIR 转换。
- BH engine 保持 `1.5.2`，BOX engine 保持 `1.0.0`，framework 使用 `2.0.0`。
- 保持 BH schema `BH-COMPILATION-REPORT-1.4`、`BH-BATCH-MANIFEST-1.4` 和
  `BH-MANUFACTURING-IR-1.1`。
- 保持 BOX schema `BOX-COMPILATION-REPORT-4.0` 以及 Project2 proof、writer 和
  saved-DXF validator。
- `D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf`、
  `D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf` 和
  `D:\DevData\项目2_BOX_dxf` 始终只读。
- 保持 `samples\bh_pairs` 字节不变；其中 20 对输入/参考已与 v1.5.2 完全一致。
- 只有唯一族、领域原生 proof 和 saved-DXF closure 是制造安全门。
- 配置失败、人工拆板、工程复核和系统失败分别分流。
- 未先征得用户许可，不安装或同步依赖；此前只用 `.venv\Scripts\python.exe`。
- Windows 只用于开发和本地测试；最终发布门禁必须在 Linux 拆板 Worker 环境运行，
  Windows 的精确 platform skip 不得被写成 Linux 发布通过。
- 不 commit、push、merge、tag 或 publish；每个任务用 diff/status 检查点结束。
- 保留工作树中与本计划无关的既有改动。

---

## 文件结构

### 新建

- `src/steel_dxf_split/application/__init__.py` — application 导出。
- `src/steel_dxf_split/application/contracts.py` — 证据、授权、处置、工件、结果和错误类型。
- `src/steel_dxf_split/application/classification.py` — 只读唯一族判型。
- `src/steel_dxf_split/application/adapters.py` — 固定 BH/BOX adapter。
- `src/steel_dxf_split/application/compiler.py` — 有序单图工作流。
- `src/steel_dxf_split/application/batch.py` — 隔离 worker、staging、提升和 manifest。
- `src/steel_dxf_split/bh/**` — 61 个 v1.5.2 源码/package-data 文件。
- `src/steel_dxf_split/bh/UPSTREAM.json` — 来源和允许适配清单。
- `tools/verify_bh_v152_source.py` — 来源一致性校验器。
- `tests/bh_v152/**` — 迁移后的 v1.5.2 内核级测试。
- `tests/test_bh_v152_package.py`
- `tests/test_application_contracts.py`
- `tests/test_application_classification.py`
- `tests/test_application_routing.py`
- `tests/test_application_cli.py`
- `tests/test_application_batch.py`
- `tests/test_dual_domain_architecture.py`

### 修改

- `src/steel_dxf_split/__init__.py`
- `src/steel_dxf_split/cli.py`
- `src/steel_dxf_split/batch_cli.py`
- `src/steel_dxf_split/box/compiler.py`
- `pyproject.toml`
- `uv.lock`
- `README.md`
- 导入公共入口的 BOX 验证脚本和语料工具。

### 替代测试通过后删除

- 根级旧 BH 模块、旧通用拆板模块、`pipeline.py` 和 `profile_detection.py`。
- 已被 v1.5.2 测试面替代的旧 BH 测试。
- 已删除的顶层 BOX 模块继续保持删除。

---

### Task 1: 原样迁入 BH v1.5.2 包

**文件：**
- 新建：`src/steel_dxf_split/bh/**`
- 新建：`src/steel_dxf_split/bh/UPSTREAM.json`
- 新建：`tests/test_bh_v152_package.py`
- 修改：`pyproject.toml`

**接口：**
- 输入：`D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2\src\steel_dxf_split`。
- 输出：可导入的 `steel_dxf_split.bh`，`__version__ == "1.5.2"`，保留全部原生
  BH 能力。

- [x] **Step 1: 先写迁移失败测试**

~~~python
# tests/test_bh_v152_package.py
from __future__ import annotations

import json
from importlib.resources import files


def test_relocated_bh_package_is_v151() -> None:
    from steel_dxf_split import bh

    assert bh.__version__ == "1.5.2"
    assert callable(bh.split_dxf)


def test_relocated_bh_release_evidence_is_packaged() -> None:
    artifact = files("steel_dxf_split.bh").joinpath(
        "release_evidence/project_tekla_bh_dxf_v1.json"
    )
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["schema"].startswith("BH-")


def test_relocated_bh_provenance_declares_only_three_adaptations() -> None:
    payload = json.loads(
        files("steel_dxf_split.bh")
        .joinpath("UPSTREAM.json")
        .read_text(encoding="utf-8")
    )
    assert payload["tag"] == "v1.5.2"
    assert payload["commit"] == "302dd73fa4b92f1d39486063c15dd49227e58b8a"
    assert payload["adapted_files"] == [
        "batch_cli.py",
        "bh_release_evidence.py",
        "layered_cli.py",
    ]
~~~

- [x] **Step 2: 运行并确认 RED**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_bh_v152_package.py -q
~~~

预期：收集阶段以 `ModuleNotFoundError: No module named 'steel_dxf_split.bh'` 失败。

- [x] **Step 3: 校验上游权威来源**

~~~powershell
git -C "D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2" status --short
git -C "D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2" describe --tags --exact-match
git -C "D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2" rev-parse HEAD
~~~

预期：状态干净，精确 tag 为 `v1.5.2`，HEAD 为
`302dd73fa4b92f1d39486063c15dd49227e58b8a`。

- [x] **Step 4: 机械复制权威包**

先 resolve 并输出 source/target 绝对路径：

~~~powershell
$source = (Resolve-Path "D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2\src\steel_dxf_split").Path
$target = Join-Path (Resolve-Path ".").Path "src\steel_dxf_split\bh"
Write-Output "source=$source"
Write-Output "target=$target"
New-Item -ItemType Directory -Force -Path $target | Out-Null
Get-ChildItem -LiteralPath $source -Force | Copy-Item -Destination $target -Recurse -Force
$files = Get-ChildItem -LiteralPath $target -Recurse -File |
  Where-Object { $_.FullName -notmatch "__pycache__" -and $_.Extension -ne ".pyc" }
$files.Count
~~~

预期：`60`。

- [x] **Step 5: 只做三处包路径适配**

使用 `apply_patch`：

~~~python
# src/steel_dxf_split/bh/batch_cli.py
source_root = str(Path(__file__).resolve().parents[2])
# 子进程模块：
"steel_dxf_split.bh.cli"
# manifest 源根：
str(Path(__file__).resolve().parents[2])

# src/steel_dxf_split/bh/bh_release_evidence.py
artifact = files("steel_dxf_split.bh").joinpath(resource_name)

# src/steel_dxf_split/bh/layered_cli.py
"steel_dxf_split.bh.layered_cli"
~~~

不得修改 BH geometry、proof、schema、writer、validator、route 或版本。

- [x] **Step 6: 增加来源元数据**

~~~json
{
  "schema": "BH-UPSTREAM-SOURCE-1.0",
  "tag": "v1.5.2",
  "tag_object": "0198a06d5a5e6ffe8d81f58df5a81a6d56cb756c",
  "commit": "302dd73fa4b92f1d39486063c15dd49227e58b8a",
  "source_file_count": 61,
  "adapted_files": [
    "batch_cli.py",
    "bh_release_evidence.py",
    "layered_cli.py"
  ],
  "adaptation_policy": "package paths only; no domain semantics"
}
~~~

- [x] **Step 7: 注册 package data**

~~~toml
[tool.setuptools.package-data]
"steel_dxf_split.bh" = ["release_evidence/*.json", "UPSTREAM.json"]
~~~

- [x] **Step 8: 验证 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_bh_v152_package.py -q
~~~

预期：`3 passed`。

- [x] **Step 9: 无提交检查点**

~~~powershell
git diff --check
git status --short
~~~

预期：无空白错误；不 commit、不 push。

---

### Task 2: 证明 BH 来源一致性与原生行为

**文件：**
- 新建：`tools/verify_bh_v152_source.py`
- 新建：`tests/bh_v152/**`
- 新建：`scripts/bh/**`
- 新建：`docs/bh/**`
- 验证：`samples/bh_pairs/*.dxf`

**接口：**
- 输入：Task 1 迁入的 BH 包。
- 输出：`58 exact / 3 adapted / 0 missing / 0 unexpected` 来源结果和迁移后的 BH
  测试面。

- [x] **Step 1: 先写来源校验失败测试**

~~~python
# 加入 tests/test_bh_v152_package.py
from pathlib import Path

from tools.verify_bh_v152_source import verify_bh_source


def test_bh_source_diff_is_limited_to_package_paths() -> None:
    result = verify_bh_source(
        Path(r"D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2"),
        Path.cwd(),
    )
    assert result == {
        "exact": 58,
        "adapted": 3,
        "missing": 0,
        "unexpected": 0,
        "invalid_adaptations": [],
    }
~~~

- [x] **Step 2: 验证 RED**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_bh_v152_package.py::test_bh_source_diff_is_limited_to_package_paths -q
~~~

预期：因 `tools.verify_bh_v152_source` 不存在而导入失败。

- [x] **Step 3: 实现来源校验器**

~~~python
# tools/verify_bh_v152_source.py
from __future__ import annotations

from pathlib import Path

REPLACEMENTS: dict[str, tuple[tuple[bytes, bytes, int], ...]] = {
    "batch_cli.py": (
        (b"steel_dxf_split.cli", b"steel_dxf_split.bh.cli", 1),
        (b"parents[1]", b"parents[2]", 2),
    ),
    "bh_release_evidence.py": (
        (b'files("steel_dxf_split")', b'files("steel_dxf_split.bh")', 1),
    ),
    "layered_cli.py": (
        (b"steel_dxf_split.layered_cli", b"steel_dxf_split.bh.layered_cli", 1),
    ),
}


def _files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and path.name != "UPSTREAM.json"
    }


def verify_bh_source(authority: Path, integrated: Path) -> dict[str, object]:
    source = _files(authority / "src/steel_dxf_split")
    target = _files(integrated / "src/steel_dxf_split/bh")
    missing = sorted(set(source) - set(target))
    unexpected = sorted(set(target) - set(source))
    exact = 0
    adapted = 0
    invalid: list[str] = []
    for relative in sorted(set(source) & set(target)):
        expected = source[relative].read_bytes()
        replacements = REPLACEMENTS.get(relative, ())
        replacement_contract_ok = True
        for old, new, expected_count in replacements:
            if expected.count(old) != expected_count:
                invalid.append(relative)
                replacement_contract_ok = False
                break
            expected = expected.replace(old, new)
        if not replacement_contract_ok:
            continue
        if target[relative].read_bytes() != expected:
            invalid.append(relative)
        elif replacements:
            adapted += 1
        else:
            exact += 1
    return {
        "exact": exact,
        "adapted": adapted,
        "missing": len(missing),
        "unexpected": len(unexpected),
        "invalid_adaptations": invalid,
    }
~~~

- [x] **Step 4: 验证 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_bh_v152_package.py -q
~~~

预期：`4 passed`。

- [ ] **Step 5: 运行未改动的上游测试套件**

~~~powershell
$old = $env:PYTHONPATH
try {
  $env:PYTHONPATH = "D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2\src;D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2"
  .\.venv\Scripts\python.exe -m pytest "D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2\tests" -q -W error
} finally {
  $env:PYTHONPATH = $old
}
~~~

预期：在 BH v1.5.2 声明支持的 Linux 环境中退出码 `0`，没有 warning 被提升成错误。
当前 Windows 工作站缺少 `os.O_DIRECTORY`；若仅
`test_artifact_io.py` 中直接覆盖该 Linux 原子目录同步合同的测试因此失败，必须原样
记录为 platform blocked，不得修改冻结 BH 实现来伪造上游 GREEN，最终发布仍需在
Linux 门禁重跑。

若桌面工具的单次命令窗口不足以容纳整套测试，可先收集精确 node ID，再按排序后的
互斥测试文件集在独立进程中执行；要求每个收集节点恰好覆盖一次、各分片均以
`-W error` 退出 0，并记录合并统计。检查点与日志只写系统临时目录，不写上游仓库。
这属于执行隔离，不得修改、跳过或放松任何上游测试。

- [ ] **Step 6: 复制内核测试，只改 namespace import**

排除以下只检查上游仓库形状的测试：

~~~text
test_bh_only_surface.py
test_documentation_contract.py
test_platform_contract.py
test_repository_health.py
~~~

执行：

~~~powershell
$source = "D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2\tests"
$target = "tests\bh_v152"
New-Item -ItemType Directory -Force -Path $target | Out-Null
Get-ChildItem -LiteralPath $source -File -Filter "*.py" |
  Where-Object { $_.Name -notin @("test_bh_only_surface.py", "test_documentation_contract.py", "test_platform_contract.py", "test_repository_health.py") } |
  Copy-Item -Destination $target -Force
Get-ChildItem -LiteralPath $target -File -Filter "*.py" | ForEach-Object {
  $text = Get-Content -Raw -Encoding UTF8 -LiteralPath $_.FullName
  $text = $text.Replace("steel_dxf_split.", "steel_dxf_split.bh.")
  $text = $text.Replace("from steel_dxf_split import", "from steel_dxf_split.bh import")
  $text = $text -replace "(?m)^import steel_dxf_split$", "import steel_dxf_split.bh as steel_dxf_split"
  [System.IO.File]::WriteAllText($_.FullName, $text, [System.Text.UTF8Encoding]::new($false))
}
~~~

运行 `rg -n "steel_dxf_split(?!\.bh)" tests\bh_v152 --pcre2`，逐项检查所有剩余命中。

迁移后的 `test_artifact_io.py` 可只对依赖 `os.O_DIRECTORY` 的精确测试增加
`os.name == "nt"` 条件 skip，原因必须明确写为 BH v1.5.2 Linux-only 原子目录同步
合同；Linux 上仍执行原断言。不得扩大到模块级、无条件 skip 或修改生产实现。

- [ ] **Step 7: 复制 BH 运维文档和脚本**

~~~powershell
$upstream = "D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2"
New-Item -ItemType Directory -Force -Path "docs\bh\releases" | Out-Null
Copy-Item "$upstream\docs\ARCHITECTURE.md" "docs\bh\ARCHITECTURE.md"
Copy-Item "$upstream\docs\INPUT_OUTPUT_CONTRACT.md" "docs\bh\INPUT_OUTPUT_CONTRACT.md"
Copy-Item "$upstream\docs\REVIEW_WORKFLOW.md" "docs\bh\REVIEW_WORKFLOW.md"
Copy-Item "$upstream\docs\TEKLA_EXPORT_PROFILE.md" "docs\bh\TEKLA_EXPORT_PROFILE.md"
Copy-Item "$upstream\docs\VALIDATION.md" "docs\bh\VALIDATION.md"
Copy-Item "$upstream\docs\releases\v1.5.2.md" "docs\bh\releases\v1.5.2.md"
Copy-Item "$upstream\scripts" "scripts\bh" -Recurse -Force
~~~

机械改写脚本 import 为 `steel_dxf_split.bh.*`，worker module 改到 BH namespace；
保留 release 断言和 `18/2/0`。

- [ ] **Step 8: 运行迁移后的内核测试套件**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\bh_v152 -q -W error
~~~

预期：退出码 `0`；Windows 可出现上段声明的 2 个条件 skip。import/resource 错误只能
通过 packaging 适配修复。

- [ ] **Step 9: 无提交检查点**

~~~powershell
git diff --check
git status --short
~~~

预期：无空白错误；不 commit、不 push。

---

### Task 3: 定义统一合同和处置类型

**文件：**
- 新建：`src/steel_dxf_split/application/__init__.py`
- 新建：`src/steel_dxf_split/application/contracts.py`
- 新建：`tests/test_application_contracts.py`

**接口：**
- 输入：不使用任何领域几何类型。
- 输出：`ProfileFamily`、`ClassificationDecision`、`SplitAuthority`、
  `SplitDisposition`、`ArtifactRef`、`DomainReportRef`、`SplitOutcome` 和
  `SplitSystemError`。

- [ ] **Step 1: 先写合同失败测试**

~~~python
# tests/test_application_contracts.py
from pathlib import Path

from steel_dxf_split.application.contracts import (
    FRAMEWORK_VERSION,
    ROUTING_SCHEMA,
    ProfileFamily,
    SplitAuthority,
    SplitDisposition,
)


def test_authority_does_not_infer_an_unprovided_family() -> None:
    authority = SplitAuthority.tekla_single_part(
        bh_profile="project_tekla_bh_dxf_v1"
    )
    assert authority.profile_for(ProfileFamily.BH) == "project_tekla_bh_dxf_v1"
    assert authority.profile_for(ProfileFamily.BOX) is None


def test_box_attestation_does_not_create_box_authority() -> None:
    authority = SplitAuthority.tekla_single_part(
        box_release_attestation=Path("box-release.json")
    )
    assert authority.profile_for(ProfileFamily.BOX) is None


def test_dispositions_are_not_collapsed_into_review() -> None:
    assert {item.value for item in SplitDisposition} == {
        "auto_accepted",
        "review_required",
        "manual_split_required",
        "unprocessable",
        "configuration_blocked",
    }


def test_routing_envelope_has_independent_framework_identity() -> None:
    assert ROUTING_SCHEMA == "DXF-SPLIT-ROUTING-1.0"
    assert FRAMEWORK_VERSION == "2.0.0"
~~~

- [ ] **Step 2: 验证 RED**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_application_contracts.py -q
~~~

预期：因 `steel_dxf_split.application` 不存在而收集失败。

- [ ] **Step 3: 实现合同类型**

~~~python
# src/steel_dxf_split/application/contracts.py
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

ROUTING_SCHEMA = "DXF-SPLIT-ROUTING-1.0"
FRAMEWORK_VERSION = "2.0.0"


class ProfileFamily(StrEnum):
    BH = "BH"
    BOX = "BOX"


class ClassificationStatus(StrEnum):
    IDENTIFIED = "identified"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class SplitDisposition(StrEnum):
    AUTO_ACCEPTED = "auto_accepted"
    REVIEW_REQUIRED = "review_required"
    MANUAL_SPLIT_REQUIRED = "manual_split_required"
    UNPROCESSABLE = "unprocessable"
    CONFIGURATION_BLOCKED = "configuration_blocked"


@dataclass(frozen=True, slots=True)
class ProfileEvidence:
    family: ProfileFamily
    raw_text: str
    normalized_text: str
    entity_type: str
    handle: str | None
    x: float
    y: float
    block_path: tuple[str, ...]
    rule_id: str
    dimensions: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ClassificationDecision:
    family: ProfileFamily | None
    status: ClassificationStatus
    evidence: tuple[ProfileEvidence, ...]
    diagnostic_codes: tuple[str, ...]
    detector_version: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class SplitAuthority:
    bh_profile: str | None = None
    box_profile: str | None = None
    box_release_attestation: Path | None = None

    @classmethod
    def tekla_single_part(
        cls,
        *,
        bh_profile: str | None = None,
        box_profile: str | None = None,
        box_release_attestation: Path | None = None,
    ) -> "SplitAuthority":
        return cls(bh_profile, box_profile, box_release_attestation)

    def profile_for(self, family: ProfileFamily) -> str | None:
        return self.bh_profile if family is ProfileFamily.BH else self.box_profile


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    kind: str
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class DomainReportRef:
    family: ProfileFamily
    schema: str
    engine_version: str
    source_commit: str
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class SplitOutcome:
    schema: str
    framework_version: str
    input_path: Path
    source_sha256: str
    classification: ClassificationDecision
    disposition: SplitDisposition
    diagnostic_codes: tuple[str, ...]
    processing_seconds: float
    artifacts: tuple[ArtifactRef, ...] = ()
    domain_report: DomainReportRef | None = None

    @property
    def production_path(self) -> Path | None:
        return next(
            (item.path for item in self.artifacts if item.kind == "production_dxf"),
            None,
        )


class SplitSystemError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
~~~

~~~python
# src/steel_dxf_split/application/__init__.py
from .contracts import SplitAuthority, SplitDisposition, SplitOutcome

__all__ = ["SplitAuthority", "SplitDisposition", "SplitOutcome"]
~~~

- [ ] **Step 4: 验证 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_application_contracts.py -q
~~~

预期：`4 passed`。

- [ ] **Step 5: 无提交检查点**

~~~powershell
git diff --check
git status --short
~~~

预期：无空白错误；不 commit、不 push。

---

### Task 4: 建立基于证据的自动判型

**文件：**
- 新建：`src/steel_dxf_split/application/classification.py`
- 新建：`tests/test_application_classification.py`

**接口：**
- 输入：Task 3 的合同类型。
- 输出：`classify_dxf(path: str | Path) -> ClassificationDecision`。

- [ ] **Step 1: 先写判型失败测试**

~~~python
# tests/test_application_classification.py
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.application.classification import classify_dxf
from steel_dxf_split.application.contracts import (
    ClassificationStatus,
    ProfileFamily,
)


def _save(tmp_path: Path, *texts: str) -> Path:
    document = ezdxf.new()
    modelspace = document.modelspace()
    for index, value in enumerate(texts):
        modelspace.add_text(value, dxfattribs={"insert": (index, 0)})
    path = tmp_path / "source.dxf"
    document.saveas(path)
    return path


@pytest.mark.parametrize(
    ("text", "family"),
    [
        ("BH600*300*12*20", ProfileFamily.BH),
        ("WH600-650×300×12×20", ProfileFamily.BH),
        ("BOX600*500*20*25", ProfileFamily.BOX),
    ],
)
def test_complete_profiles_identify_one_family(
    tmp_path: Path, text: str, family: ProfileFamily
) -> None:
    result = classify_dxf(_save(tmp_path, text))
    assert result.status is ClassificationStatus.IDENTIFIED
    assert result.family is family


def test_same_family_duplicates_do_not_require_review(tmp_path: Path) -> None:
    result = classify_dxf(
        _save(tmp_path, "BOX600*500*20*25", "BOX600×500×20×25")
    )
    assert result.status is ClassificationStatus.IDENTIFIED
    assert result.family is ProfileFamily.BOX


@pytest.mark.parametrize(
    "texts",
    [
        ("BH600*300*12*20", "BOX600*500*20*25"),
        ("BOX600*500*20*25", "BH600*300*12*20"),
    ],
)
def test_family_conflict_is_order_independent(
    tmp_path: Path, texts: tuple[str, str]
) -> None:
    result = classify_dxf(_save(tmp_path, *texts))
    assert result.status is ClassificationStatus.CONFLICT
    assert result.family is None
    assert result.diagnostic_codes == ("DXF.CLASSIFICATION.FAMILY_CONFLICT",)


def test_unknown_text_does_not_guess_a_family(tmp_path: Path) -> None:
    result = classify_dxf(_save(tmp_path, "BH member note without dimensions"))
    assert result.status is ClassificationStatus.UNKNOWN
    assert result.family is None
~~~

- [ ] **Step 2: 验证 RED**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_application_classification.py -q
~~~

预期：因 `application.classification` 不存在而收集失败。

- [ ] **Step 3: 实现判型器**

~~~python
# src/steel_dxf_split/application/classification.py
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import unicodedata

import ezdxf
from ezdxf.entities import DXFEntity, Insert, Text
from ezdxf.lldxf.encoding import decode_mif_to_unicode

from .contracts import (
    ClassificationDecision,
    ClassificationStatus,
    ProfileEvidence,
    ProfileFamily,
)

DETECTOR_VERSION = "DXF-PROFILE-CLASSIFIER-1.0"
_BH = re.compile(
    r"(?<![A-Z0-9])(?P<family>BH|WH|HW|HM|HN|H)\s*"
    r"(?P<h1>\d+(?:\.\d+)?)"
    r"(?:\s*[-~～—]\s*(?P<h2>\d+(?:\.\d+)?))?\s*[xX×*]\s*"
    r"(?P<b>\d+(?:\.\d+)?)\s*[xX×*]\s*(?P<tw>\d+(?:\.\d+)?)"
    r"\s*[xX×*]\s*(?P<tf>\d+(?:\.\d+)?)(?![A-Z0-9])",
    re.IGNORECASE,
)
_BOX = re.compile(
    r"\bBOX\s*(?P<h>\d+(?:\.\d+)?)\s*[xX×*]\s*"
    r"(?P<b>\d+(?:\.\d+)?)\s*[xX×*]\s*(?P<tw>\d+(?:\.\d+)?)"
    r"\s*[xX×*]\s*(?P<tf>\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)


def _normalize(value: str) -> str:
    value = decode_mif_to_unicode(value)
    value = ezdxf.decode_dxf_unicode(value)
    value = value.replace("%%c", "Φ").replace("%%C", "Φ")
    value = value.replace("¦µ", "Φ").replace("¦μ", "Φ").replace("\\P", " ")
    value = re.sub(r"\\[A-Za-z][^;]*;", "", value)
    value = unicodedata.normalize("NFKC", value.replace("{", "").replace("}", ""))
    return " ".join(value.strip().split())


def _walk(
    entity: DXFEntity, block_path: tuple[str, ...] = ()
) -> list[tuple[DXFEntity, tuple[str, ...]]]:
    if entity.dxftype() != "INSERT":
        return [(entity, block_path)]
    assert isinstance(entity, Insert)
    result: list[tuple[DXFEntity, tuple[str, ...]]] = []
    for child in entity.virtual_entities():
        result.extend(_walk(child, (*block_path, str(entity.dxf.name))))
    return result


def classify_dxf(path: str | Path) -> ClassificationDecision:
    source = Path(path).resolve(strict=True)
    if source.suffix.casefold() != ".dxf" or not source.is_file():
        raise ValueError("classification input must be one ordinary .dxf file")
    source_hash = sha256(source.read_bytes()).hexdigest()
    document = ezdxf.readfile(source)
    evidence: list[ProfileEvidence] = []
    try:
        for top in document.modelspace():
            for entity, block_path in _walk(top):
                if entity.dxftype() not in {"TEXT", "MTEXT"}:
                    continue
                raw = (
                    str(entity.dxf.text)
                    if isinstance(entity, Text)
                    else str(entity.plain_text())
                )
                normalized = _normalize(raw)
                insert = getattr(entity.dxf, "insert", (0.0, 0.0, 0.0))
                for family, rule_id, pattern in (
                    (ProfileFamily.BH, "PROFILE.BH.COMPLETE_V1", _BH),
                    (ProfileFamily.BOX, "PROFILE.BOX.COMPLETE_V1", _BOX),
                ):
                    match = pattern.search(normalized)
                    if match is None:
                        continue
                    values = match.groupdict()
                    evidence.append(
                        ProfileEvidence(
                            family=family,
                            raw_text=raw,
                            normalized_text=normalized,
                            entity_type=entity.dxftype(),
                            handle=getattr(entity.dxf, "handle", None),
                            x=float(insert[0]),
                            y=float(insert[1]),
                            block_path=block_path,
                            rule_id=rule_id,
                            dimensions=tuple(
                                float(value)
                                for value in (
                                    values.get("h1") or values.get("h"),
                                    values.get("h2"),
                                    values["b"],
                                    values["tw"],
                                    values["tf"],
                                )
                                if value is not None
                            ),
                        )
                    )
    finally:
        del document
    families = {item.family for item in evidence}
    if not families:
        return ClassificationDecision(
            None,
            ClassificationStatus.UNKNOWN,
            (),
            ("DXF.CLASSIFICATION.FAMILY_UNKNOWN",),
            DETECTOR_VERSION,
            source_hash,
        )
    if len(families) > 1:
        return ClassificationDecision(
            None,
            ClassificationStatus.CONFLICT,
            tuple(evidence),
            ("DXF.CLASSIFICATION.FAMILY_CONFLICT",),
            DETECTOR_VERSION,
            source_hash,
        )
    family = next(iter(families))
    return ClassificationDecision(
        family,
        ClassificationStatus.IDENTIFIED,
        tuple(evidence),
        (),
        DETECTOR_VERSION,
        source_hash,
    )
~~~

- [ ] **Step 4: 增加文本传输与来源测试**

显式覆盖 nested INSERT + MTEXT、MIF/Unicode/旧 cp936 直径符号、文件名含 `BOX`
但正文无完整规格、非普通文件/非 `.dxf` 输入，以及判型前后 source SHA-256 相等。
每项断言精确 status、diagnostic code、block path、解析尺寸和不变哈希。补一个
`ABH600*300*12*20` 用例，证明族名边界不会误匹配。

- [ ] **Step 5: 验证 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_application_classification.py -q
~~~

预期：全部判型用例通过，源文件没有变化。

- [ ] **Step 6: 无提交检查点**

~~~powershell
git diff --check
git status --short
~~~

预期：无空白错误；不 commit、不 push。

---

### Task 5: 建立固定领域适配器和单图深模块

**文件：**
- 新建：`src/steel_dxf_split/application/adapters.py`
- 新建：`src/steel_dxf_split/application/compiler.py`
- 新建：`tests/test_application_routing.py`
- 修改：`src/steel_dxf_split/box/compiler.py`
- 修改：`tests/box_v1/test_compiler_passes.py`

**接口：**
- 输入 `ClassificationDecision`、`SplitAuthority`、输入路径、输出目录；
- 输出一个 `SplitOutcome`；
- 内部只允许调用 `BHV152Adapter` 或 `BoxProject2V100Adapter` 之一；
- 不公开 adapter 注册、backend 参数或 fallback 入口。

- [ ] **Step 1: 先写唯一路由和处置映射的失败测试**

`tests/test_application_routing.py` 必须检查 adapter 精确调用次数与结果：

| 场景 | BH 调用 | BOX 调用 | 结果 |
|---|---:|---:|---|
| 唯一 BH + 有效 authority | 1 | 0 | BH 原生处置 |
| 唯一 BOX + 有效 authority | 0 | 1 | BOX 原生处置 |
| unknown/conflict | 0 | 0 | `manual_split_required` |
| 对应 authority 缺失/失效 | 0 | 0 | `configuration_blocked` |
| 任一 adapter 抛异常 | 只调用该 adapter 一次 | 另一方为 0 | `SplitSystemError` |

固定映射为：

~~~python
BH_ROUTES = {
    "production": SplitDisposition.AUTO_ACCEPTED,
    "review_required": SplitDisposition.REVIEW_REQUIRED,
    "rejected": SplitDisposition.UNPROCESSABLE,
}
BOX_ROUTES = {
    "auto_accepted": SplitDisposition.AUTO_ACCEPTED,
    "review_required": SplitDisposition.REVIEW_REQUIRED,
}
~~~

另测普通 warning 不升级处置；BOX 原生 proof 为 `auto_accept` 但 attestation
缺失只能是 `configuration_blocked`；adapter 返回后源哈希变化必须抛出
`DXF.SOURCE.CHANGED_DURING_PROCESSING` 且不返回工件。

- [ ] **Step 2: 验证 RED**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_application_routing.py -q
~~~

预期：因两个 application 模块尚不存在而收集失败。

- [ ] **Step 3: 为 BOX rejected 增加有类型的内核出口**

在 `box/compiler.py` 增加 `BoxCompilationRejected`，构造参数为
`BoxCoreCompilation` 并保留为 `core` 属性。`compile_box()` 在
`compile_box_core()` 后、`deliver_box_compilation()` 前识别原生
`proof_report.disposition == rejected` 并抛出该异常。增加
`tests/box_v1/test_compiler_passes.py` 回归，证明拒绝时没有生产/复核 DXF。
不得修改 Project2 frontend、analysis、solve、ManufacturingIR、proof、writer 或
saved-DXF validator。

- [ ] **Step 4: 实现两个固定 adapter**

`application/adapters.py` 定义内部 `DomainRun`，字段为 family、engine version、
source commit、raw route、diagnostic codes、production/review/source-copy/preview
路径和 report 路径。

- `BHV152Adapter` 固定版本 `1.5.2`、commit
  `302dd73fa4b92f1d39486063c15dd49227e58b8a`，构造
  `bh.bh_knowledge.BHSourceContract` 与 `bh.pipeline.SplitOptions`，只调用一次
  `bh.pipeline.split_dxf`；
- `BoxProject2V100Adapter` 固定版本 `1.0.0`、commit
  `5a2be1a82eb7235bcff62d97a13d2937f9ad026b`，构造
  `box.contracts.BoxSourceContract` 与 `box.compiler.BoxCompileConfig`，只调用
  一次 `box.compiler.compile_box`；
- 两者均校验 report family/schema/version/route 和声明工件；
- adapter 只提取跨领域事实，不转换领域 IR/proof；
- `BoxCompilationRejected` 保留原生 proof 诊断并固定映射为 `unprocessable`，
  不调用 BH、不生成生产/复核 DXF；
- 定义内部 `DomainConfigurationError(code, message)`；只有 source contract 或
  attestation 的缺失/不匹配使用它，application 将其映射为
  `configuration_blocked`；
- 显式 `if family is ...` 选择 adapter，不提供注册表、插件发现或动态 backend。

- [ ] **Step 5: 实现有序的单图 compiler**

在 `application/compiler.py` 实现：

~~~python
def split_dxf(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    authority: SplitAuthority,
) -> SplitOutcome:
    ...
~~~

执行顺序固定为：源 SHA-256 → 判型一次 → unknown/conflict 直接人工拆板 →
校验对应 authority → 调用一个 adapter 一次 → 固定处置映射 → 重算源哈希 →
校验所有工件位于输出根目录且计算哈希 → 校验领域报告 → 返回统一包络。
构造结果时明确写入 `schema=DXF-SPLIT-ROUTING-1.0`、
`framework_version=2.0.0` 和单调时钟 `processing_seconds`。

制造输出只能由三项条件阻止：唯一族/对应授权、领域原生 proof、领域原生 saved-DXF
closure。普通 warning、同族重复判型证据和非阻塞诊断不得新增 review gate。

至少定义这些稳定诊断码：

- `DXF.CLASSIFICATION.FAMILY_UNKNOWN`、`DXF.CLASSIFICATION.FAMILY_CONFLICT`；
- `DXF.AUTHORITY.BH_PROFILE_MISSING`、`DXF.AUTHORITY.BOX_PROFILE_MISSING`；
- `DXF.AUTHORITY.BOX_ATTESTATION_MISSING`、`DXF.AUTHORITY.BOX_ATTESTATION_INVALID`；
- `DXF.DOMAIN.REPORT_CONTRACT_INVALID`、`DXF.ARTIFACT.OUTSIDE_OUTPUT_ROOT`；
- `DXF.SOURCE.CHANGED_DURING_PROCESSING`。

I/O、领域未分类异常和工件合同错误包装成带 `retryable` 的
`SplitSystemError`，不得转成 `review_required`。

- [ ] **Step 6: 验证 GREEN 与领域回归**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_application_routing.py tests\box_v1\test_compiler_passes.py -q
~~~

预期：唯一调用、状态分流、源哈希和 BOX typed rejection 全部通过。

- [ ] **Step 7: 无提交检查点**

~~~powershell
git diff --check
git status --short
~~~

预期：无空白错误；不 commit、不 push。

---

### Task 6: 收敛包根 API 和统一单图 CLI

**文件：**
- 修改：`src/steel_dxf_split/__init__.py`
- 修改：`src/steel_dxf_split/cli.py`
- 修改：`src/steel_dxf_split/application/__init__.py`
- 新建：`tests/test_application_cli.py`

**接口：**
- 本任务先让包根公开 `SplitAuthority`、`SplitDisposition`、`SplitOutcome` 和
  `split_dxf`；Task 7 实现批次后再加入 `split_batch`；
- framework `__version__ == "2.0.0"`；
- 领域内核版本仍分别为 `bh.__version__ == "1.5.2"` 与
  `box.__version__ == "1.0.0"`。

- [ ] **Step 1: 先写公共 API 与 CLI 失败测试**

`tests/test_application_cli.py` 覆盖：

1. 导入包根时可取得本任务的四个公共符号，且不会急切导入 BH/BOX 几何模块；
2. `--help` 为简体中文，包含两个 source profile 参数和 BOX attestation 参数；
3. BH 输入构造只含 BH profile 的 `SplitAuthority`；
4. BOX 输入构造 BOX profile + attestation 的 `SplitAuthority`；
5. JSON 输出完整序列化 schema、framework version、processing time、
   classification evidence、disposition、diagnostic codes、artifacts 和 domain report；
6. default 模式对所有结构化处置返回 0；
7. `--require-auto-accept` 仅对 `auto_accepted` 返回 0，其他结构化处置返回 1；
8. `SplitSystemError` 输出结构化 stderr JSON 并返回 2；
9. parser 不存在 `--backend`、`--family`、`--legacy`、`--fallback`、
   `--full-role-names`、`--no-clean`、`--no-review` 或 `--no-sheet`。

- [ ] **Step 2: 验证 RED**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_application_cli.py -q
~~~

预期：旧包根版本、旧参数和旧 pipeline 导入导致失败。

- [ ] **Step 3: 实现惰性包根导出**

`src/steel_dxf_split/__init__.py` 只保存 framework 版本及通过
`__getattr__` 实现的惰性 application 导出。`application/__init__.py` 先导出
contracts 与 `split_dxf`；Task 7 再补 `split_batch`。不要从包根导出领域 IR、
adapter 或 `compile_box_core`。

- [ ] **Step 4: 重写统一 CLI**

统一 `steel-dxf-split` 接受一个输入、`--output-dir` 及以下可选 authority：

~~~text
--authorize-tekla-bh-single-part-profile project_tekla_bh_dxf_v1
--authorize-tekla-box-single-part-profile project_tekla_box_dxf_v1
--box-release-attestation PATH
--require-auto-accept
~~~

CLI 不提供强制 family；自动判型决定使用哪个 authority。输出一个 UTF-8 JSON object，
`ensure_ascii=False`，路径转成绝对字符串，枚举转成值。错误 JSON 至少包含
`status="system_failed"`、`code`、`message` 和 `retryable`。自然语言 help 和错误提示
使用简体中文，技术标识符保持英文。

- [ ] **Step 5: 验证 GREEN 和 CLI smoke**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_application_cli.py tests\test_application_routing.py -q
.\.venv\Scripts\python.exe -m steel_dxf_split.cli --help
~~~

预期：测试通过；help 只有统一入口参数，没有旧算法/展示参数。

- [ ] **Step 6: 无提交检查点**

~~~powershell
git diff --check
git status --short
~~~

预期：无空白错误；不 commit、不 push。

---

### Task 7: 实现混合 BH/BOX 批次事务

**文件：**
- 新建：`src/steel_dxf_split/application/batch.py`
- 修改：`src/steel_dxf_split/application/__init__.py`
- 修改：`src/steel_dxf_split/__init__.py`
- 修改：`src/steel_dxf_split/batch_cli.py`
- 新建：`tests/test_application_batch.py`

**接口：**

~~~python
def split_batch(
    input_paths: Iterable[str | Path],
    output_dir: str | Path,
    *,
    authority: SplitAuthority,
    timeout_seconds: float = 300.0,
) -> BatchOutcome:
    ...
~~~

manifest schema 固定为 `DXF-SPLIT-BATCH-MANIFEST-1.0`，单图路由包络 schema
固定为 `DXF-SPLIT-ROUTING-1.0`。

- [ ] **Step 1: 先写批次事务失败测试**

`tests/test_application_batch.py` 至少覆盖：

1. BH、BOX、unknown、conflict 混合输入全部处理，不因非 auto-accept 提前停止；
2. 每个输入通过新进程调用统一 `steel_dxf_split.cli`，不直接导入领域 adapter；
3. 同一文件的相对/绝对路径别名、大小写别名和重复输入在 worker 前被拒绝；
4. 不同输入若会生成相同最终 item identity，则在 worker 前报告冲突；
5. staging 位于 output root 同盘，成功项以单个 item 目录 rename 提升；
6. 超时记录 `timeout` 并只删除该项 staging；
7. 非零系统退出记录 `system_failed`，不变成 `review_required`；
8. worker framework/engine version 漂移拒绝提升；
9. 声明工件集合少于或多于实际文件集合时拒绝提升；
10. 工件路径逃出 staging 或包含 symlink/reparse point 时拒绝提升；
11. worker 前后输入 SHA-256 变化时拒绝提升；
12. staging 路径只做等价前缀替换，proof、ManufacturingIR、处置、版本和非路径
    字符串逐字段不变；
13. manifest 每完成一项原子 checkpoint；
14. `require_auto_accept` 只影响最终 exit policy，不改写 item status。
15. Task 完成后包根可惰性取得 `split_batch`，且不会导入任一领域几何模块。

- [ ] **Step 2: 验证 RED**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_application_batch.py -q
~~~

预期：`application.batch` 尚不存在，测试失败。

- [ ] **Step 3: 定义批次合同并冻结输入集合**

`application/batch.py` 定义 `BatchItemOutcome` 与 `BatchOutcome`。每项记录 input、
source SHA-256、item id、status、routing envelope、system error、duration 和 final
item root。

启动任何 worker 前，父进程 resolve 全部普通 `.dxf`，记录 `st_dev/st_ino`
（可用时）、规范化绝对路径、大小写折叠路径和 SHA-256，并拒绝重复身份与最终目录
冲突。staging 使用 `output/.dxf-split-staging/<batch-id>/<item-id>`；最终目录使用
`output/items/<safe-stem>-<source-sha256[:12]>`，保证同盘目录 rename。

- [ ] **Step 4: 实现隔离 worker 与监督**

每项使用当前解释器启动：

~~~text
python -m steel_dxf_split.cli INPUT --output-dir ITEM_STAGE [authority flags]
~~~

传入 authority 的原值，不推断缺失项。用 `subprocess.Popen` 与
`communicate(timeout=...)` 捕获 UTF-8 输出；超时终止进程树、等待退出并清理该项。
只接受一个 `DXF-SPLIT-ROUTING-1.0` JSON object；每个 worker 必须报告 framework
version、engine version 和 source commit。

- [ ] **Step 5: 实现验证、路径改写与提升**

父进程按顺序执行：

1. 重算源哈希并校验 routing envelope、领域 report 与版本；
2. 枚举 staging 中全部普通文件并拒绝 symlink/reparse point；
3. 要求声明工件集合与实际集合精确相等，且 resolve 后仍在 item staging；
4. 递归遍历 JSON，仅把精确以 item staging 开头的路径字符串替换成 final item root；
5. 重写并落盘领域 report，重算其 SHA-256 后更新 routing envelope；
6. 把最终 routing envelope 写成 parent-owned `routing-report.json`；它不把自身列入
   artifacts，避免自引用哈希；
7. flush 后以同盘 directory rename 提升整个 item；
8. 原子写入 `output/dxf-split-batch-manifest.json` checkpoint。

manifest 分别统计 `auto_accepted`、`review_required`、
`manual_split_required`、`unprocessable`、`configuration_blocked`、
`system_failed` 和 `timeout`，不得合并为 passed/failed 或 review。

- [ ] **Step 6: 重写 batch CLI**

`steel-dxf-split-batch` 接受多个 input、相同 authority flags、
`--timeout-seconds` 和 `--require-auto-accept`。输出完整 manifest JSON。
同时把 `split_batch` 加入 application 与包根的惰性公开 API。

退出码：无系统故障且默认模式为 0；strict 模式存在非 auto-accept 为 1；存在
`system_failed`、`timeout` 或批次合同错误为 2。

- [ ] **Step 7: 验证 GREEN**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_application_batch.py tests\test_application_cli.py -q
~~~

预期：批次事务、完整状态分流、超时清理和 manifest checkpoint 全部通过。

- [ ] **Step 8: 无提交检查点**

~~~powershell
git diff --check
git status --short
~~~

预期：无空白错误；不 commit、不 push。

---

### Task 8: 删除旧根算法并完成 2.0.0 包装

**文件：**
- 新建：`tests/test_dual_domain_architecture.py`
- 修改：`pyproject.toml`
- 修改：`uv.lock`
- 修改：`README.md`
- 修改：仍引用旧根模块的 BOX 验证脚本/语料工具
- 删除：下列旧根算法模块与已被 v1.5.2 测试面替代的旧 BH 测试

- [ ] **Step 1: 先写架构失败测试**

`tests/test_dual_domain_architecture.py` 使用 AST 与文件清单验证：

1. `steel_dxf_split.bh` 与 `steel_dxf_split.box` 都可导入且版本正确；
2. 两个领域都不 import 对方，也不 import `steel_dxf_split.application`；
3. `application.classification/contracts/compiler/batch` 不含几何推理实现；
4. 只有 `application.adapters` 可同时引用两个领域；
5. 根包不存在旧 BH、旧 generic splitter 或兼容转发模块；
6. 没有动态 backend 注册、fallback、voting、legacy solver 或 IR conversion；
7. BOX 来源 manifest 继续报告 23 exact、7 declared patches，且 0 missing、
   0 changed、0 unexpected；
8. BH source verifier 仍报告 57 个原样文件、3 个允许适配文件；
9. package data 能通过 `importlib.resources.files("steel_dxf_split.bh")` 读取；
10. console entry point 精确指向统一入口和 BH 原生入口。

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_dual_domain_architecture.py -q
~~~

预期：旧根文件和旧 entry point 尚在，测试失败。

- [ ] **Step 2: 删除前运行替代面门禁**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_bh_v152_package.py tests\bh_v152 tests\box_v1 tests\test_application_contracts.py tests\test_application_classification.py tests\test_application_routing.py tests\test_application_cli.py tests\test_application_batch.py -q -W error
~~~

预期：替代测试面全部通过后才允许删除。若失败，先修复对应迁移，不删除旧文件。

- [ ] **Step 3: 更新 packaging、entry points 与中文 README**

`pyproject.toml` 设置 framework version `2.0.0`，保留领域依赖约束，补上
BH package data，并定义：

~~~toml
[project.scripts]
steel-dxf-split = "steel_dxf_split.cli:main"
steel-dxf-split-batch = "steel_dxf_split.batch_cli:main"
steel-dxf-split-bh = "steel_dxf_split.bh.cli:main"
steel-dxf-split-bh-batch = "steel_dxf_split.bh.batch_cli:main"
steel-dxf-inspect = "steel_dxf_split.bh.layered_cli:main"
steel-dxf-weld-allowance = "steel_dxf_split.bh.weld_allowance_cli:main"
steel-dxf-verify-weld-allowance = "steel_dxf_split.bh.weld_allowance_release:main"

[tool.setuptools.package-data]
"steel_dxf_split.bh" = ["release_evidence/*.json"]
~~~

README 用简体中文解释自动判型、两个 source profile、BOX attestation、五种结构化处置、
system failure/timeout、统一 CLI、批次 CLI 和 BH 原生工具。明确没有强制 family、
backend 或 fallback。

- [ ] **Step 4: 清除生产引用后删除旧根实现**

先运行：

~~~powershell
rg -n "steel_dxf_split\.(bh_|pipeline|profile_detection|dxf_io|extractor|geometry|layout|models|reference_geometry|text)" src tests tools scripts
rg -n "from \.(bh_|pipeline|profile_detection|dxf_io|extractor|geometry|layout|models|reference_geometry|text)|from steel_dxf_split import .*SplitOptions" src tests tools scripts -g "!src/steel_dxf_split/bh/**" -g "!src/steel_dxf_split/box/**"
~~~

把仍需要的调用迁移到 `application`、`bh` 或 `box` 明确路径；随后用
`apply_patch` 删除以下根模块：

~~~text
bh_annotations.py
bh_compare.py
bh_compiler.py
bh_constraints.py
bh_contracts.py
bh_corpus.py
bh_extractor.py
bh_fingerprint.py
bh_frontend.py
bh_geometry.py
bh_hypothesis.py
bh_ir.py
bh_knowledge.py
bh_models.py
bh_ontology.py
bh_passes.py
bh_pipeline.py
bh_reasoning.py
bh_risks.py
bh_semantics.py
bh_solver.py
bh_text.py
bh_validator.py
bh_writer.py
dxf_io.py
extractor.py
geometry.py
layout.py
models.py
pipeline.py
profile_detection.py
reference_geometry.py
text.py
~~~

删除这些已由 `tests/bh_v152` 替代的旧 BH 测试：

~~~text
tests/test_bh_compiler_v08.py
tests/test_bh_risk_analysis_v11.py
tests/test_bh_semantic_contract_v11.py
tests/test_bh_semantic_core_v10.py
tests/test_bh_semantic_solver_v10.py
tests/test_bh_supervised_pairs.py
tests/test_bh_supervised_pairs_v06.py
tests/test_bh_supervised_pairs_v07.py
tests/test_bh_supervised_pairs_v09.py
~~~

保留 `samples/bh_pairs` 原字节不动；不创建任何根级兼容 shim。

- [ ] **Step 5: 离线更新 lock，不同步环境**

~~~powershell
D:\DevData\uv\bin\uv.exe lock --offline
~~~

预期：只更新 project metadata/lock。若命令需要网络、安装或 sync，立即停止并先向用户
申请；不要运行 `uv sync`、`pip install` 或 `conda install`。

- [ ] **Step 6: 验证架构 GREEN 与无悬空 import**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_dual_domain_architecture.py -q
.\.venv\Scripts\python.exe -c "import steel_dxf_split; import steel_dxf_split.bh; import steel_dxf_split.box; print(steel_dxf_split.__version__)"
rg -n "steel_dxf_split\.(bh_|pipeline|profile_detection|dxf_io|extractor|geometry|layout|models|reference_geometry|text)" src tests tools scripts
~~~

预期：架构测试通过，版本输出 `2.0.0`，最后一个 `rg` 无旧根 import 命中。

- [ ] **Step 7: 无提交检查点**

~~~powershell
git diff --check
git status --short
~~~

预期：只包含本计划范围内的迁移；不 commit、不 push。

---

### Task 9: 执行双领域最终验收并形成报告

**文件：**
- 新建：`docs/superpowers/reports/2026-07-21-bh-box-domain-authority-integration-validation.md`
- 修改：`scripts/verify_box_v1_fusion.py`（仅在缺少可复现 attestation 输出时）
- 修改：验收发现的本计划范围内缺陷文件

- [ ] **Step 1: 冻结只读语料哈希和实现身份**

验收开始前记录：

- `samples/bh_pairs` 中 40 个 DXF 的相对路径、长度和 SHA-256；
- BOX 权威 before/after 两目录各 20 个 DXF 的相对路径、长度和 SHA-256；
- `D:\DevData\项目2_BOX_dxf` 中 30 个 DXF 的相对路径、长度和 SHA-256；
- framework、BH、BOX 三个版本与两个 source commit；
- 当前 `git status --short`。

快照写入系统临时目录，不写入任一语料目录。验收结束后用相同算法重算并要求完全相等。

- [ ] **Step 2: 验证 BH/BOX 来源身份**

~~~powershell
.\.venv\Scripts\python.exe tools\verify_bh_v152_source.py --upstream "D:\Documents\Codex\worktrees\steel-dxf-split\v1.5.2"
.\.venv\Scripts\python.exe scripts\verify_box_v1_source.py --upstream "D:\Documents\Codex\worktrees\box-dxf-split\v1.0.0"
~~~

预期：

- BH：57 exact、3 adapted、0 missing、0 unexpected；
- BOX：23 exact、7 declared patches、0 missing、0 changed、0 unexpected。

- [ ] **Step 3: 运行完整自动测试**

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q -W error
~~~

预期：0 failed；BOX 现有 11 条平台/字体/可选语料条件 skip 可以保留，但新增测试不得
通过无条件 skip 或放松断言隐藏失败。记录 collected/passed/skipped 总数和耗时。

- [ ] **Step 4: 验证 BH 20 图统一入口基线**

在系统临时输出目录中，用统一 `split_batch()` 处理
`samples/bh_pairs/*_拆板前.dxf`，authority 只提供
`bh_profile="project_tekla_bh_dxf_v1"`。

预期：

- 20 个输入全部选择 BH，BOX adapter 调用 0 次；
- `18 auto_accepted / 2 review_required / 0 unprocessable`；
- review 文件仍只有 `2b1-cb-40_拆板前.dxf` 与 `2b2-cb-10_拆板前.dxf`；
- BH 原生 report schema、proof、writer 回读、preview、分层和焊接余量回归保持；
- `samples/bh_pairs` 输入与参考哈希不变。

- [ ] **Step 5: 重新认证 BOX 20 对并验证统一入口**

先运行只读权威比较：

~~~powershell
.\.venv\Scripts\python.exe scripts\verify_box_v1_fusion.py --inputs "D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf" --references "D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf" --output "$env:TEMP\box-v1-fusion-acceptance.json"
~~~

预期 20/20 制造几何与孔归属匹配，19 个 `auto_accept`，`2b2-cb-2` 因重叠投影的
直接源面枚举不完整而明确 fail-closed，before/after 均 unchanged。不得为恢复
`20/20 auto_accept` 放松来源证明。

若现有脚本不能输出与当前 implementation fingerprint 绑定的 release attestation，
先为它增加 `--release-attestation PATH`，配套测试固定以下规则：按文件名排序、前 10
对为 calibration、后 10 对为 acceptance；只有 20 对制造几何与孔归属全部匹配、
19 个生产放行且 `2b2-cb-2` 保持声明过的 fail-closed 时，才调用
`write_box_release_attestation()`；manifest/gate fingerprint 使用 canonical JSON
SHA-256。attestation 只写系统临时目录。

随后用统一 `split_batch()` 和该 attestation 处理 20 个 before 输入。预期 20 个都选择
BOX、BH adapter 调用 0 次；19 个生产输出的领域 proof 与 saved-DXF closure 通过，
`2b2-cb-2` 保持原生 fail-closed 且不写生产 DXF，统一处置与原生领域处置一致。

- [ ] **Step 6: 验证项目 2 独立 30 图语料**

用同一临时 attestation 和 BOX profile 通过统一 `split_batch()` 处理
`D:\DevData\项目2_BOX_dxf`。

预期：

- 30/30 选择 BOX；
- 30/30 `auto_accepted`；
- 30/30 saved DXF 可重新打开并与 ManufacturingIR 闭合；
- 无 BH 调用、无 fallback、无 legacy solver、无结果拼接；
- 30 个输入哈希不变。

- [ ] **Step 7: 验证混合批次和失败分类**

在临时目录准备一个已验收 BH、一个已验收 BOX、一个无完整规格的 unknown，以及一个
同时含完整 BH/BOX 规格的 conflict，运行统一 batch：

- BH → 原生 BH 处置；
- BOX → 原生 BOX 处置；
- unknown/conflict → `manual_split_required` 且不调用任何领域；
- 另用缺失 BOX attestation 的调用验证 `configuration_blocked`；
- 用 monkeypatch/integration fixture 验证系统异常为 `system_failed`、超时为
  `timeout`；
- manifest 各队列、计数、版本、工件哈希和最终路径一致。

- [ ] **Step 8: 运行静态、编译和 wheel 检查**

先运行不需要安装的检查：

~~~powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP "dxf-split-pycache"
.\.venv\Scripts\python.exe -m compileall -q -f src tests tools scripts
git diff --check
~~~

当前环境没有 Ruff/Mypy。执行到此处先向用户申请一次临时工具下载/环境使用许可；获批后
运行：

~~~powershell
D:\DevData\uv\bin\uv.exe run --frozen ruff check src tests tools scripts
D:\DevData\uv\bin\uv.exe tool run --from mypy==1.18.2 mypy src\steel_dxf_split\application src\steel_dxf_split\cli.py src\steel_dxf_split\batch_cli.py src\steel_dxf_split\box\compiler.py
~~~

定向 Mypy 只覆盖新 application、统一入口与改动过的 BOX seam；上游 BH v1.5.2 按来源
测试与行为验收，不为追求全仓 Mypy 数字修改冻结代码。记录全仓 Mypy（若另跑）仅作
诊断，不作为虚假通过声明。

构建 wheel 同样先征得许可，再用离线/冻结依赖方式构建。检查 wheel：

- 含 `application/`、完整 `bh/`、完整 `box/` 和 BH release JSON；
- 不含已删除旧根算法或测试/语料；
- 安装到临时隔离环境后，七个 console entry point 的 `--help` 正常；
- framework/BH/BOX 三个版本分别正确。

- [ ] **Step 9: 重算哈希并写最终验证报告**

重算三套语料快照并与 Step 1 逐字节比较。报告必须记录：

- 实际修改和最终目录结构；
- BH source fidelity、原生测试与 `18/2/0`；
- BOX source fidelity（23 exact + 7 declared patches）、完整测试、权威 20/20
  几何与孔归属（19 auto-accept + 1 fail-closed）和项目 2 `30/30`；
- 混合批次处置矩阵；
- pytest、Ruff、Mypy、compileall、wheel 和 `git diff --check` 的实际结果；
- skip 的逐类原因；
- 未获许可或无法运行的检查，明确标为未执行，不能写成通过；
- 语料 before/after 哈希相等；
- 剩余风险与未做的 commit/push/merge。

- [ ] **Step 10: 最终无提交检查点**

~~~powershell
git diff --check
git status --short
git diff --stat
~~~

完成条件是设计文档第 16 节十项同时满足。此任务仍不 commit、不 push、不 merge；
由用户决定后续分支收尾和并入主干。
