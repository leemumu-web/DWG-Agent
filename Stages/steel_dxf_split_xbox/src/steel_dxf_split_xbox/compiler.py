from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import XboxSourceContract, XboxSplitError, member_name
from .pairing import verify_paired_geometry

XBOX_REPORT_SCHEMA = "steel-dxf-split-xbox-report/1"
AUTO_ACCEPTED_ROUTE = "auto_accepted"
REVIEW_ROUTE = "review_required"


@dataclass(frozen=True, slots=True)
class XboxItemResult:
    source: Path
    member: str
    status: str
    task_dir: Path
    report_item: dict[str, object]


@dataclass(frozen=True, slots=True)
class XboxBatchResult:
    input_dir: Path
    output_dir: Path
    items: tuple[XboxItemResult, ...]

    @property
    def success_count(self) -> int:
        return sum(1 for item in self.items if item.status == AUTO_ACCEPTED_ROUTE)

    @property
    def rejected_count(self) -> int:
        return sum(1 for item in self.items if item.status != AUTO_ACCEPTED_ROUTE)

    @property
    def exit_code(self) -> int:
        return 1 if self.rejected_count else 0


def compile_xbox(
    input_path: str | Path,
    *,
    config: "BoxCompileConfig",
) -> "BoxCompilationResult":
    """Compile XBOX through the vendored closed-box geometry engine."""

    from dataclasses import replace

    from .box.compiler import compile_box

    xbox_config = replace(config, family="XBOX")
    return compile_box(input_path, config=xbox_config)


def discover_input_files(input_dir: Path) -> tuple[Path, ...]:
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise XboxSplitError(
            "XBOX_SPLIT_INPUT_INVALID",
            "XBOX 拆板输入路径必须是普通目录，且不能是符号链接。",
        )
    inputs = tuple(
        sorted(
            (
                path
                for path in input_dir.iterdir()
                if path.is_file()
                and not path.is_symlink()
                and path.suffix.casefold() == ".dxf"
            ),
            key=lambda path: path.name.casefold(),
        )
    )
    if not inputs:
        raise XboxSplitError(
            "XBOX_SPLIT_INPUT_EMPTY",
            "XBOX 拆板输入目录中没有可处理的 DXF 文件。",
        )
    seen: set[str] = set()
    for path in inputs:
        name = member_name(path)
        key = name.casefold()
        if key in seen:
            raise XboxSplitError(
                "XBOX_SPLIT_INPUT_NAME_CONFLICT",
                f"XBOX 拆板输入目录存在会写入同一任务目录的重名 DXF：{path.name}",
            )
        seen.add(key)
    return inputs


def _publish_auto_accepted(
    compiled: object,
    *,
    input_path: Path,
    output_dir: Path,
    work_dir: Path,
) -> tuple[Path, dict[str, object]]:
    from .box.weld_allowance import apply_weld_allowance
    from .paired_output import validate_paired_outputs

    member = member_name(input_path)
    task_dir = output_dir / "auto_accepted" / "xbox" / member
    task_dir.mkdir(parents=True, exist_ok=True)

    production_path = getattr(compiled, "production_path")
    report_path = getattr(compiled, "report_path")
    if production_path is None or not Path(production_path).is_file():
        raise XboxSplitError(
            "XBOX_SPLIT_NORMAL_OUTPUT_MISSING",
            f"{input_path.name} 的 XBOX 正常拆板产物缺失。",
        )
    # Run allowance + pair proof on the native artifact chain (report
    # bindings pin those paths), then publish renamed copies together.
    allowance_work = work_dir / "allowance"
    allowance_work.mkdir(parents=True, exist_ok=True)
    allowance_output = allowance_work / f"{member}_weld_allowance.dxf"
    allowance_report_path = allowance_work / f"{member}_weld_allowance_report.json"
    generated = apply_weld_allowance(
        Path(production_path),
        Path(report_path),
        allowance_output,
        allowance_report_path,
    )
    if Path(generated.output_path) != allowance_output or Path(generated.report_path) != (
        allowance_report_path
    ):
        raise XboxSplitError(
            "XBOX_SPLIT_ALLOWANCE_ARTIFACT_INVALID",
            f"{input_path.name} 的焊接余量处理器返回了无效产物集。",
        )
    pair_validation = validate_paired_outputs(
        Path(production_path),
        allowance_output,
        allowance_report_path,
        family="XBOX",
    )
    if pair_validation.get("ok") is not True:
        raise XboxSplitError(
            "XBOX_SPLIT_PAIR_VALIDATION_FAILED",
            f"{input_path.name} 的成对产物校验未通过。",
        )
    pair_proof = verify_paired_geometry(
        Path(production_path), allowance_output
    )

    normal_dxf = task_dir / f"{member}_正常拆板.dxf"
    weld_dxf = task_dir / f"{member}_余量增长.dxf"
    report_json = task_dir / f"{member}_report.json"
    weld_report_json = task_dir / f"{member}_weld_allowance_report.json"
    shutil.copyfile(production_path, normal_dxf)
    shutil.copyfile(report_path, report_json)
    shutil.copyfile(allowance_output, weld_dxf)
    shutil.copyfile(allowance_report_path, weld_report_json)

    report_item: dict[str, object] = {
        "source": input_path.name,
        "member": member,
        "status": AUTO_ACCEPTED_ROUTE,
        "family": "XBOX",
        "task_dir": task_dir.name,
        "automation_route": AUTO_ACCEPTED_ROUTE,
        "outputs": {
            "normal_dxf": normal_dxf.name,
            "weld_allowance_dxf": weld_dxf.name,
            "report": report_json.name,
            "weld_allowance_report": weld_report_json.name,
        },
        "pair_proof": pair_proof,
        "native_report": getattr(compiled, "report"),
    }
    return task_dir, report_item


def _publish_manual_review(
    compiled: object,
    *,
    input_path: Path,
    output_dir: Path,
) -> tuple[Path, dict[str, object]]:
    member = member_name(input_path)
    task_dir = output_dir / "manual_review" / "xbox" / member
    task_dir.mkdir(parents=True, exist_ok=True)

    review_path = getattr(compiled, "review_path")
    report_path = getattr(compiled, "report_path")
    report_item: dict[str, object] = {
        "source": input_path.name,
        "member": member,
        "status": "manual_review",
        "family": "XBOX",
        "task_dir": task_dir.name,
        "automation_route": REVIEW_ROUTE,
        "outputs": {},
        "native_report": getattr(compiled, "report"),
    }
    if review_path is not None and Path(review_path).is_file():
        review_dxf = task_dir / f"{member}_复核候选.dxf"
        shutil.copyfile(review_path, review_dxf)
        report_item["outputs"]["review_dxf"] = review_dxf.name
    if report_path is not None and Path(report_path).is_file():
        report_json = task_dir / f"{member}_report.json"
        shutil.copyfile(report_path, report_json)
        report_item["outputs"]["report"] = report_json.name
    return task_dir, report_item


def compile_xbox_batch(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    release_attestation_path: str | Path | None = None,
) -> XboxBatchResult:
    """Compile a frozen directory of XBOX drawings into paired DXF results."""

    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    if output_dir.is_symlink():
        raise XboxSplitError(
            "XBOX_SPLIT_OUTPUT_INVALID",
            "XBOX 拆板输出路径不能是符号链接。",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise XboxSplitError(
            "XBOX_SPLIT_OUTPUT_INVALID",
            "XBOX 拆板输出路径必须是普通目录。",
        )
    try:
        output_dir.resolve().relative_to(input_dir.resolve())
    except ValueError:
        pass
    else:
        raise XboxSplitError(
            "XBOX_SPLIT_DIRECTORIES_NESTED",
            "XBOX 拆板输入目录与输出目录不得相同或互相嵌套。",
        )
    inputs = discover_input_files(input_dir)

    items: list[XboxItemResult] = []
    for input_path in inputs:
        member = member_name(input_path)
        existing = output_dir / "auto_accepted" / "xbox" / member
        existing_review = output_dir / "manual_review" / "xbox" / member
        if not overwrite and (existing.exists() or existing_review.exists()):
            raise XboxSplitError(
                "XBOX_SPLIT_OUTPUT_EXISTS",
                f"{member} 的拆板结果已存在；如需覆盖请使用 --overwrite。",
            )
        with TemporaryDirectory(prefix=".xbox-task-", dir=output_dir) as temporary:
            native_output = Path(temporary) / "native"
            native_output.mkdir()
            compiled = compile_xbox(
                input_path,
                config=_compile_config(
                    native_output,
                    release_attestation_path=release_attestation_path,
                ),
            )
            native_report = getattr(compiled, "report")
            route = native_report.get("automation_route")
            if route == AUTO_ACCEPTED_ROUTE:
                task_dir, report_item = _publish_auto_accepted(
                    compiled,
                    input_path=input_path,
                    output_dir=output_dir,
                    work_dir=Path(temporary),
                )
            else:
                task_dir, report_item = _publish_manual_review(
                    compiled,
                    input_path=input_path,
                    output_dir=output_dir,
                )
        items.append(
            XboxItemResult(
                source=input_path,
                member=member,
                status=str(report_item["status"]),
                task_dir=task_dir,
                report_item=report_item,
            )
        )
    return XboxBatchResult(
        input_dir=input_dir,
        output_dir=output_dir,
        items=tuple(items),
    )


def _compile_config(
    output_dir: Path,
    *,
    release_attestation_path: str | Path | None,
) -> "BoxCompileConfig":
    from .box.compiler import BoxCompileConfig

    return BoxCompileConfig(
        output_dir=output_dir,
        source_contract=XboxSourceContract(),
        report_path=None,
        require_auto_accept=False,
        release_attestation_path=(
            Path(release_attestation_path)
            if release_attestation_path is not None
            else None
        ),
        family="XBOX",
    )


def batch_payload(batch: XboxBatchResult) -> dict[str, object]:
    return {
        "schema": XBOX_REPORT_SCHEMA,
        "input": str(batch.input_dir),
        "output_dir": str(batch.output_dir),
        "success_count": batch.success_count,
        "rejected_count": batch.rejected_count,
        "exit_code": batch.exit_code,
        "items": [item.report_item for item in batch.items],
    }


__all__ = [
    "AUTO_ACCEPTED_ROUTE",
    "REVIEW_ROUTE",
    "XBOX_REPORT_SCHEMA",
    "XboxBatchResult",
    "XboxItemResult",
    "batch_payload",
    "compile_xbox",
    "compile_xbox_batch",
    "discover_input_files",
]
