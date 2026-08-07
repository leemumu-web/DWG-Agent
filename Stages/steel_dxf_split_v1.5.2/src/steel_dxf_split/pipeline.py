from __future__ import annotations

from copy import deepcopy
import json
import os
import shutil
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from .artifact_io import fsync_directory, write_json_atomic
from .bh_knowledge import BHSourceContract
from .bh_pipeline import split_bh_dxf
from .box.contracts import BoxSourceContract
from .paired_output import (
    PairedOutputValidationError,
    validate_paired_outputs,
)


@dataclass(frozen=True, slots=True)
class SplitOptions:
    """Explicit source authority for the unified paired-output route."""

    source_contract: BHSourceContract | None = None
    box_source_contract: BoxSourceContract | None = None
    box_release_attestation: Path | None = None
    skip_png_and_json: bool = False


@dataclass(slots=True)
class SplitResult:
    """One domain-neutral result returned by the unified split worker."""

    production_path: Path | None
    review_candidate_path: Path | None
    report_path: Path
    weld_allowance_path: Path | None
    weld_allowance_report_path: Path | None
    task_dir: Path | None
    report: dict[str, object]
    family: str
    automation_route: str
    native_automation_route: str
    disposition: str
    production_ready: bool
    proof_disposition: str | None
    diagnostic_codes: tuple[str, ...]
    previews: dict[str, object] | None
    compiler_pass_seconds: float
    preview_render_seconds: float
    compiler_pass_scope: str
    search_complete: bool | None
    manufacturing_fingerprint: str | None
    supervised_match: bool | None
    mass_error_pct: float | None

    @classmethod
    def from_native(
        cls,
        *,
        production_path: Path | None,
        review_candidate_path: Path | None,
        report_path: Path,
        report: dict[str, object],
    ) -> SplitResult:
        """Normalize a native BH or BOX report at the single worker seam."""

        family = report.get("profile_family")
        if family not in {"BH", "BOX"}:
            raise ValueError("native split report has no supported profile_family")
        native_route = report.get("automation_route")
        allowed_native_routes = (
            {"production", "review_required", "rejected"}
            if family == "BH"
            else {"auto_accepted", "review_required", "rejected"}
        )
        if native_route not in allowed_native_routes:
            raise ValueError(
                f"{family} native split report has an invalid automation_route"
            )
        automation_route = (
            "auto_accepted" if native_route == "production" else str(native_route)
        )

        proof = _mapping(report.get("proof_report"))
        assessment = _mapping(report.get("automation_assessment"))
        disposition_value = (
            report.get("single_file_disposition")
            if family == "BOX"
            else assessment.get("disposition")
        )
        if not isinstance(disposition_value, str):
            disposition_value = proof.get("disposition")
        if not isinstance(disposition_value, str):
            raise ValueError(f"{family} native split report has no disposition")
        proof_disposition = proof.get("disposition")
        if proof_disposition is not None and not isinstance(proof_disposition, str):
            raise ValueError(f"{family} proof disposition is invalid")

        if production_path is not None and review_candidate_path is not None:
            raise ValueError("split result cannot contain production and review outputs")
        if automation_route == "auto_accepted" and production_path is None:
            raise ValueError("auto_accepted split result has no production output")
        if automation_route == "review_required" and review_candidate_path is None:
            raise ValueError("review_required split result has no review output")
        if automation_route == "rejected" and (
            production_path is not None or review_candidate_path is not None
        ):
            raise ValueError("rejected split result contains a manufacturable output")

        outputs = _mapping(report.get("outputs"))
        previews_value = outputs.get("previews")
        previews = previews_value if isinstance(previews_value, dict) else None
        search_complete_value = _mapping(report.get("search_status")).get(
            "search_complete"
        )
        search_complete = (
            search_complete_value if isinstance(search_complete_value, bool) else None
        )
        diagnostics = _diagnostic_codes(report, proof, family=str(family))
        compiler_pass_seconds, preview_render_seconds, compiler_pass_scope = (
            _native_timing(report, family=str(family))
        )
        manufacturing_fingerprint = _manufacturing_fingerprint(
            report, family=str(family)
        )
        supervised_value = _mapping(report.get("supervised_comparison")).get("ok")
        supervised_match = supervised_value if isinstance(supervised_value, bool) else None
        mass_error_value = _mapping(
            _mapping(report.get("validation")).get("values")
        ).get("mass_error_pct")
        mass_error_pct = _optional_finite_number(
            mass_error_value,
            name="mass_error_pct",
        )
        return cls(
            production_path=production_path,
            review_candidate_path=review_candidate_path,
            report_path=report_path,
            weld_allowance_path=None,
            weld_allowance_report_path=None,
            task_dir=None,
            report=report,
            family=str(family),
            automation_route=automation_route,
            native_automation_route=str(native_route),
            disposition=disposition_value,
            production_ready=(
                automation_route == "auto_accepted" and production_path is not None
            ),
            proof_disposition=(
                str(proof_disposition) if proof_disposition is not None else None
            ),
            diagnostic_codes=diagnostics,
            previews=previews,
            compiler_pass_seconds=compiler_pass_seconds,
            preview_render_seconds=preview_render_seconds,
            compiler_pass_scope=compiler_pass_scope,
            search_complete=search_complete,
            manufacturing_fingerprint=manufacturing_fingerprint,
            supervised_match=supervised_match,
            mass_error_pct=mass_error_pct,
        )

    def to_summary(
        self,
        *,
        input_path: str | Path,
        compiler_version: str,
        processing_seconds: float,
    ) -> dict[str, object]:
        """Return the stable JSON contract consumed by batch orchestration."""

        processing_seconds = _finite_duration(
            processing_seconds,
            name="processing_seconds",
        )
        return {
            "input": str(input_path),
            "compiler_version": compiler_version,
            "family": self.family,
            "production_clean": (
                str(self.production_path) if self.production_path is not None else None
            ),
            "review_candidate": (
                str(self.review_candidate_path)
                if self.review_candidate_path is not None
                else None
            ),
            "report": (
                str(self.report_path) if self.report_path is not None else None
            ),
            "weld_allowance": (
                str(self.weld_allowance_path)
                if self.weld_allowance_path is not None
                else None
            ),
            "weld_allowance_report": (
                str(self.weld_allowance_report_path)
                if self.weld_allowance_report_path is not None
                else None
            ),
            "task_dir": str(self.task_dir) if self.task_dir is not None else None,
            "previews": self.previews,
            "automation_route": self.automation_route,
            "native_automation_route": self.native_automation_route,
            "disposition": self.disposition,
            "production_ready": self.production_ready,
            "proof_disposition": self.proof_disposition,
            "diagnostic_codes": list(self.diagnostic_codes),
            "search_complete": self.search_complete,
            "manufacturing_fingerprint": self.manufacturing_fingerprint,
            "supervised_match": self.supervised_match,
            "mass_error_pct": self.mass_error_pct,
            "cross_lines": 0,
            "timing": {
                "clock": "time.perf_counter",
                "measurement": "monotonic_wall_clock",
                "compiler_pass_seconds": self.compiler_pass_seconds,
                "preview_render_seconds": self.preview_render_seconds,
                "processing_seconds": processing_seconds,
                "compiler_pass_scope": self.compiler_pass_scope,
                "processing_scope": (
                    "authorized_split_call_through_persisted_report"
                ),
                "preview_render_scope": (
                    "paired_input_and_output_dxf_png_rendering"
                ),
            },
        }

    @property
    def clean_path(self) -> Path | None:
        return self.production_path

    @property
    def review_path(self) -> Path | None:
        return self.review_candidate_path

    @property
    def sheet_path(self) -> None:
        return None


def _member_name(input_path: Path) -> str:
    name = (
        input_path.stem.replace("_拆板前", "")
        .replace("拆板前", "")
        .rstrip("_- ")
    )
    if not name:
        raise ValueError("input drawing has an empty member name")
    return name


def _rewrite_exact_paths(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _rewrite_exact_paths(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_exact_paths(item, replacements) for item in value]
    if isinstance(value, str):
        return replacements.get(value, value)
    return value


def _load_json_object(path: Path, *, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{description} must be a JSON object")
    return payload


def _copy_report_artifacts(
    result: SplitResult,
    staged_task: Path,
    final_task: Path,
    *,
    member: str,
    include_source: bool,
    skip_png_and_json: bool = False,
) -> tuple[dict[str, str], dict[str, object] | None, Path | None]:
    replacements: dict[str, str] = {}
    staged_previews = staged_task / "previews"
    final_previews = final_task / "previews"
    preview_payload = deepcopy(result.previews) if result.previews is not None else None
    if skip_png_and_json:
        preview_payload = None
    elif isinstance(preview_payload, dict):
        for phase in ("before", "after"):
            value = preview_payload.get(phase)
            if not isinstance(value, str):
                continue
            source = Path(value)
            if not source.is_file():
                raise ValueError(f"native {phase} preview is missing")
            staged = staged_previews / f"{member}_{phase}.png"
            final = final_previews / staged.name
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)
            replacements[str(source.resolve())] = str(final.resolve())
            preview_payload[phase] = str(final.resolve())

    staged_source: Path | None = None
    outputs = _mapping(result.report.get("outputs"))
    source_value = outputs.get("source_copy")
    if include_source and isinstance(source_value, str):
        source = Path(source_value)
        if source.is_file():
            staged_source = staged_task / f"{member}_source.dxf"
            shutil.copy2(source, staged_source)
            replacements[str(source.resolve())] = str(
                (final_task / staged_source.name).resolve()
            )
    return replacements, preview_payload, staged_source


def _promote_task_directory(
    staged_task: Path,
    final_task: Path,
    *,
    obsolete_task: Path | None = None,
) -> None:
    """Publish one task route and restore both prior routes on failure."""

    staged_task = Path(staged_task)
    final_task = Path(final_task)
    obsolete_task = Path(obsolete_task) if obsolete_task is not None else None
    if not staged_task.is_dir() or staged_task.is_symlink():
        raise ValueError("paired output staging directory is missing or symbolic")
    final_task.parent.mkdir(parents=True, exist_ok=True)
    if final_task.exists() and (final_task.is_symlink() or not final_task.is_dir()):
        raise ValueError("paired output destination must be a regular directory")
    if obsolete_task is not None:
        if obsolete_task.resolve() == final_task.resolve():
            raise ValueError("paired output routes must be different directories")
        if obsolete_task.exists() and (
            obsolete_task.is_symlink() or not obsolete_task.is_dir()
        ):
            raise ValueError("obsolete paired output route must be a regular directory")
    token = uuid4().hex
    routes = tuple(
        route for route in (final_task, obsolete_task) if route is not None
    )
    backups = {
        route: route.with_name(f".{route.name}.{token}.paired-output-backup")
        for route in routes
    }
    if any(
        backup.parent.resolve() != route.parent.resolve()
        for route, backup in backups.items()
    ):
        raise ValueError("paired output backup escaped its task parent")
    created_backups: list[tuple[Path, Path]] = []
    try:
        for route, backup in backups.items():
            if route.exists():
                os.replace(route, backup)
                created_backups.append((route, backup))
        os.replace(staged_task, final_task)
        for parent in {route.parent for route in routes}:
            if parent.is_dir():
                fsync_directory(parent)
    except Exception:
        if final_task.exists():
            shutil.rmtree(final_task)
        for route, backup in reversed(created_backups):
            if backup.exists():
                os.replace(backup, route)
        for parent in {route.parent for route in routes}:
            if parent.is_dir():
                fsync_directory(parent)
        raise
    else:
        for _route, backup in created_backups:
            if backup.exists():
                shutil.rmtree(backup)
        for parent in {route.parent for route in routes}:
            if parent.is_dir():
                fsync_directory(parent)


def _publish_successful_pair(
    result: SplitResult,
    *,
    stage_root: Path,
    output_root: Path,
    member: str,
    skip_png_and_json: bool = False,
) -> SplitResult:
    if result.production_path is None or not result.production_path.is_file():
        raise ValueError("native auto-accepted result has no complete normal DXF")
    allowance_work = stage_root / "allowance"
    allowance_work.mkdir(parents=True, exist_ok=True)
    allowance_output = allowance_work / f"{member}_weld_allowance.dxf"
    allowance_report_path = allowance_work / f"{member}_weld_allowance_report.json"

    if result.family == "BH":
        from .weld_allowance import apply_weld_allowance
    elif result.family == "BOX":
        from .box.weld_allowance import apply_weld_allowance
    else:
        raise ValueError("paired output has an unsupported family")
    generated = apply_weld_allowance(
        result.production_path,
        result.report_path,
        allowance_output,
        allowance_report_path,
    )
    if (
        Path(generated.output_path) != allowance_output
        or Path(generated.report_path) != allowance_report_path
    ):
        raise ValueError("weld allowance processor returned an invalid artifact set")
    pair_validation = validate_paired_outputs(
        result.production_path,
        allowance_output,
        allowance_report_path,
        family=result.family,
    )
    if pair_validation.get("ok") is not True:
        raise PairedOutputValidationError(
            "paired output validator did not return a passing proof"
        )

    staged_task = stage_root / "task"
    staged_task.mkdir(parents=True)
    final_task = (
        output_root / "auto_accepted" / result.family.lower() / member
    ).resolve()
    staged_normal = staged_task / f"{member}_正常拆板.dxf"
    staged_allowance = staged_task / f"{member}_余量增长.dxf"
    final_normal = final_task / staged_normal.name
    final_allowance = final_task / staged_allowance.name
    final_report = final_task / f"{member}_report.json"
    final_allowance_report = final_task / f"{member}_weld_allowance_report.json"
    shutil.copy2(result.production_path, staged_normal)
    shutil.copy2(allowance_output, staged_allowance)

    replacements, preview_payload, _ = _copy_report_artifacts(
        result,
        staged_task,
        final_task,
        member=member,
        include_source=False,
        skip_png_and_json=skip_png_and_json,
    )
    replacements.update(
        {
            str(result.production_path.resolve()): str(final_normal.resolve()),
            str(result.report_path.resolve()): str(final_report.resolve()),
            str(allowance_output.resolve()): str(final_allowance.resolve()),
            str(allowance_report_path.resolve()): str(
                final_allowance_report.resolve()
            ),
        }
    )
    updated_report = _rewrite_exact_paths(deepcopy(result.report), replacements)
    outputs = updated_report.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("native split report has no outputs mapping")
    outputs.update(
        {
            "production_clean": str(final_normal.resolve()),
            "review_candidate": None,
            "source_copy": None,
            "previews": preview_payload,
            "weld_allowance": str(final_allowance.resolve()),
            "weld_allowance_report": str(final_allowance_report.resolve()),
        }
    )
    updated_report["native_automation_route"] = result.native_automation_route
    updated_report["automation_route"] = "auto_accepted"
    updated_report["paired_output"] = {
        "schema": "STEEL-DXF-PAIRED-OUTPUT-1.0",
        "status": "auto_accepted",
        "summary_zh": "普通版与余量伸长版已从同一次拆板结果派生并成对验收通过。",
        "normal_dxf": str(final_normal.resolve()),
        "weld_allowance_dxf": str(final_allowance.resolve()),
        "validation": _rewrite_exact_paths(pair_validation, replacements),
    }

    allowance_report = _rewrite_exact_paths(
        _load_json_object(
            allowance_report_path,
            description="weld allowance report",
        ),
        replacements,
    )
    if not skip_png_and_json:
        write_json_atomic(
            staged_task / f"{member}_weld_allowance_report.json",
            allowance_report,
        )
        write_json_atomic(staged_task / f"{member}_report.json", updated_report)
    if set(path.name for path in staged_task.rglob("*.dxf")) != {
        staged_normal.name,
        staged_allowance.name,
    }:
        raise ValueError("auto-accepted task does not contain exactly one DXF pair")

    _promote_task_directory(
        staged_task,
        final_task,
        obsolete_task=(
            output_root / "manual_review" / result.family.lower() / member
        ).resolve(),
    )
    result.production_path = final_normal
    result.review_candidate_path = None
    result.report_path = final_report if not skip_png_and_json else None
    result.weld_allowance_path = final_allowance
    result.weld_allowance_report_path = (
        final_allowance_report if not skip_png_and_json else None
    )
    result.task_dir = final_task
    result.report = updated_report
    result.previews = preview_payload
    result.automation_route = "auto_accepted"
    result.production_ready = True
    return result


def _publish_manual_review(
    result: SplitResult,
    *,
    stage_root: Path,
    output_root: Path,
    member: str,
    pair_error: Exception | None = None,
    skip_png_and_json: bool = False,
) -> SplitResult:
    staged_task = stage_root / "task"
    staged_task.mkdir(parents=True, exist_ok=True)
    final_task = (
        output_root / "manual_review" / result.family.lower() / member
    ).resolve()
    candidate_source = (
        result.production_path if pair_error is not None else result.review_candidate_path
    )
    staged_candidate: Path | None = None
    final_candidate: Path | None = None
    if candidate_source is not None and candidate_source.is_file():
        suffix = "normal_candidate" if pair_error is not None else "review_candidate"
        staged_candidate = staged_task / f"{member}_{suffix}.dxf"
        final_candidate = final_task / staged_candidate.name
        shutil.copy2(candidate_source, staged_candidate)

    replacements, preview_payload, staged_source = _copy_report_artifacts(
        result,
        staged_task,
        final_task,
        member=member,
        include_source=True,
        skip_png_and_json=skip_png_and_json,
    )
    if candidate_source is not None and final_candidate is not None:
        replacements[str(candidate_source.resolve())] = str(final_candidate.resolve())
    final_report = final_task / f"{member}_report.json"
    replacements[str(result.report_path.resolve())] = str(final_report.resolve())
    updated_report = _rewrite_exact_paths(deepcopy(result.report), replacements)
    pair_error_zh: str | None = None
    if pair_error is not None:
        diagnostic_codes = list(result.diagnostic_codes)
        diagnostic_codes.append("PAIRED_WELD_ALLOWANCE_FAILED")
        if "missing its weld allowance XDATA binding" in str(pair_error):
            diagnostic_codes.append("WELD_ALLOWANCE_CONTRACT_UNAVAILABLE")
            manufacturing = _mapping(updated_report.get("manufacturing_ir"))
            unbound_roles = [
                str(plate.get("role"))
                for plate in manufacturing.get("plates", [])
                if isinstance(plate, dict)
                and plate.get("weld_allowance_contract") is None
                and isinstance(plate.get("role"), str)
            ]
            role_names = {
                "web": "腹板",
                "upper_flange": "上翼板",
                "lower_flange": "下翼板",
            }
            rendered_roles = "、".join(
                dict.fromkeys(role_names.get(role, role) for role in unbound_roles)
            )
            pair_error_zh = (
                f"{rendered_roles or '板件'}轮廓无法证明唯一的余量伸长端，"
                "余量增长版未生成。"
            )
        else:
            pair_error_zh = "余量增长版生成或成对校验未通过，未形成正式配对文件。"
        result.diagnostic_codes = tuple(dict.fromkeys(diagnostic_codes))
        updated_report["diagnostic_codes"] = list(result.diagnostic_codes)
    outputs = updated_report.get("outputs")
    if not isinstance(outputs, dict):
        outputs = {}
        updated_report["outputs"] = outputs
    outputs.update(
        {
            "production_clean": None,
            "review_candidate": (
                str(final_candidate.resolve()) if final_candidate is not None else None
            ),
            "source_copy": (
                str((final_task / staged_source.name).resolve())
                if staged_source is not None
                else None
            ),
            "previews": preview_payload,
            "weld_allowance": None,
            "weld_allowance_report": None,
        }
    )
    updated_report["native_automation_route"] = result.native_automation_route
    updated_report["automation_route"] = "manual_review"
    updated_report["paired_output"] = {
        "schema": "STEEL-DXF-PAIRED-OUTPUT-1.0",
        "status": "manual_review",
        "summary_zh": (
            "余量伸长版未通过成对验收，本图未形成正式配对结果。"
            if pair_error is not None
            else "领域拆板结果未达到自动发布条件，本图未形成正式配对结果。"
        ),
        "failure_stage": (
            "paired_weld_allowance" if pair_error is not None else "native_split"
        ),
        "error_type": type(pair_error).__name__ if pair_error is not None else None,
        "error": str(pair_error) if pair_error is not None else None,
        "error_zh": pair_error_zh,
    }
    if not skip_png_and_json:
        write_json_atomic(staged_task / f"{member}_report.json", updated_report)
    _promote_task_directory(
        staged_task,
        final_task,
        obsolete_task=(
            output_root / "auto_accepted" / result.family.lower() / member
        ).resolve(),
    )

    result.production_path = None
    result.review_candidate_path = final_candidate
    result.report_path = final_report if not skip_png_and_json else None
    result.weld_allowance_path = None
    result.weld_allowance_report_path = None
    result.task_dir = final_task
    result.report = updated_report
    result.previews = preview_payload
    result.automation_route = "manual_review"
    result.production_ready = False
    return result


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _finite_duration(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} is missing or invalid")
    result = float(value)
    if not isfinite(result) or result < 0.0:
        raise ValueError(f"{name} is missing or invalid")
    return result


def _optional_finite_number(value: object, *, name: str) -> float | None:
    if value is None:
        return None
    return _finite_duration(value, name=name)


def _diagnostic_codes(
    report: dict[str, object],
    proof: dict[str, Any],
    *,
    family: str,
) -> tuple[str, ...]:
    if family == "BH":
        values = report.get("diagnostic_codes", [])
        if not isinstance(values, list):
            raise ValueError("BH diagnostic_codes is invalid")
        return tuple(str(value) for value in values if isinstance(value, str))
    obligations = proof.get("obligations", [])
    if not isinstance(obligations, list):
        raise ValueError("BOX proof obligations are invalid")
    return tuple(
        code
        for item in obligations
        if isinstance(item, dict)
        for code in (item.get("diagnostic_code"),)
        if isinstance(code, str) and code
    )


def _native_timing(
    report: dict[str, object],
    *,
    family: str,
) -> tuple[float, float, str]:
    if family == "BOX":
        timing = _mapping(report.get("timing"))
        return (
            0.0,
            _finite_duration(
                timing.get("preview_render_seconds", 0.0),
                name="BOX preview_render_seconds",
            ),
            "project2_box_core",
        )
    compiler = _mapping(report.get("compiler"))
    stages = compiler.get("stages", [])
    if not isinstance(stages, list):
        raise ValueError("BH compiler stages are invalid")
    compiler_pass_seconds = 0.0
    for stage in stages:
        if not isinstance(stage, dict):
            raise ValueError("BH compiler stage is invalid")
        compiler_pass_seconds += _finite_duration(
            stage.get("duration_ms", 0.0),
            name="BH compiler stage duration_ms",
        ) / 1000.0
    preview_rendering = _mapping(report.get("preview_rendering"))
    return (
        compiler_pass_seconds,
        _finite_duration(
            preview_rendering.get("render_seconds", 0.0),
            name="BH preview render_seconds",
        ),
        "eight_compiler_passes",
    )


def _manufacturing_fingerprint(
    report: dict[str, object],
    *,
    family: str,
) -> str | None:
    if family == "BH":
        value = _mapping(report.get("semantic_fingerprints")).get(
            "manufacturing_ir"
        )
    else:
        value = _mapping(report.get("manufacturing_ir")).get("fingerprint")
    if value is None:
        value = _mapping(report.get("manufacturing_ir")).get("fingerprint")
    return value if isinstance(value, str) else None


def split_classified_dxf(
    input_path: str | Path,
    output_dir: str | Path,
    options: SplitOptions,
    *,
    family: str,
) -> SplitResult:
    """Dispatch one frozen classification to its matching domain core."""

    input_path = Path(input_path)
    output_dir = Path(output_dir)
    if family not in {"BH", "BOX"}:
        raise ValueError("已分类拆板类型只能是 BH 或 BOX")
    if family == "BH" and options.source_contract is None:
        raise ValueError("BH 分类输入缺少 BH source contract")
    if family == "BOX" and options.box_source_contract is None:
        raise ValueError("BOX 分类输入缺少 BOX source contract")

    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("paired output root must be a regular directory")
    member = _member_name(input_path)
    from .weld_allowance import WeldAllowanceProcessingError
    from .box.weld_allowance import BoxWeldAllowanceProcessingError

    with TemporaryDirectory(
        prefix=".steel-dxf-task-",
        dir=output_dir,
    ) as temporary:
        stage_root = Path(temporary)
        native_output = stage_root / "native"
        if family == "BH":
            assert options.source_contract is not None
            production_path, review_candidate_path, report_path, report = (
                split_bh_dxf(
                    input_path,
                    native_output,
                    source_contract=options.source_contract,
                    manual_reference_path=None,
                    report_path=None,
                    require_auto_accept=False,
                )
            )
        else:
            assert options.box_source_contract is not None
            from .box.compiler import BoxCompileConfig, compile_box

            compiled = compile_box(
                input_path,
                config=BoxCompileConfig(
                    output_dir=native_output,
                    source_contract=options.box_source_contract,
                    report_path=None,
                    require_auto_accept=False,
                    release_attestation_path=options.box_release_attestation,
                ),
            )
            production_path = compiled.production_path
            review_candidate_path = compiled.review_path
            report_path = compiled.report_path
            report = compiled.report

        result = SplitResult.from_native(
            production_path=production_path,
            review_candidate_path=review_candidate_path,
            report_path=report_path,
            report=report,
        )
        if result.automation_route != "auto_accepted":
            return _publish_manual_review(
                result,
                stage_root=stage_root,
                output_root=output_dir,
                member=member,
                skip_png_and_json=options.skip_png_and_json,
            )
        try:
            return _publish_successful_pair(
                result,
                stage_root=stage_root,
                output_root=output_dir,
                member=member,
                skip_png_and_json=options.skip_png_and_json,
            )
        except (
            WeldAllowanceProcessingError,
            BoxWeldAllowanceProcessingError,
            PairedOutputValidationError,
        ) as exc:
            return _publish_manual_review(
                result,
                stage_root=stage_root,
                output_root=output_dir,
                member=member,
                pair_error=exc,
                skip_png_and_json=options.skip_png_and_json,
            )
