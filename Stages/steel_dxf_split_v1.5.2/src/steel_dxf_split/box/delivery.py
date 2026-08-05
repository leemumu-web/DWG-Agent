from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import TYPE_CHECKING

from .contracts import BOX_AUTO_ACCEPTED_ROUTE, BOX_COMPILATION_REPORT_SCHEMA
from .provenance import (
    BOX_CORE_COMMIT,
    BOX_CORE_TAG,
    BOX_CORE_VERSION,
)
from .release import BoxVerifiedReleaseAttestation
from .validator import validate_saved_dxf
from .writer import BoxLayout, OutputPurpose, write_box_clean

if TYPE_CHECKING:
    from .compiler import BoxCompileConfig, BoxCoreCompilation


@dataclass(frozen=True, slots=True)
class BoxDeliveryArtifacts:
    production_path: Path | None
    review_path: Path | None
    report_path: Path
    report: dict[str, object]


def _base_name(input_path: Path) -> str:
    return (
        input_path.stem.replace("_拆板前", "").replace("拆板前", "").rstrip("_- ")
    )


def _failed_checks(report: dict[str, object]) -> tuple[str, ...]:
    checks = report.get("checks")
    if not isinstance(checks, dict):
        return ("missing_checks",)
    return tuple(
        sorted(str(name) for name, passed in checks.items() if passed is not True)
    )


def _fsync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    descriptor = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_staged(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(
            payload,
            stream,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _promote_staged_files(
    staged_to_final: tuple[tuple[Path, Path], ...],
    *,
    backup_dir: Path,
    remove_paths: tuple[Path, ...] = (),
) -> None:
    """Install one staged artifact set and restore the previous set on failure."""

    backup_dir.mkdir(parents=True, exist_ok=True)
    targets = tuple(dict.fromkeys(final for _, final in staged_to_final))
    stale = tuple(path for path in dict.fromkeys(remove_paths) if path not in targets)
    backups: list[tuple[Path, Path]] = []
    promoted: list[Path] = []
    try:
        for index, final_path in enumerate((*targets, *stale)):
            if not final_path.is_file():
                continue
            backup = backup_dir / f"{index:03d}-{final_path.name}.bak"
            os.replace(final_path, backup)
            backups.append((backup, final_path))
        for staged_path, final_path in staged_to_final:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_path, final_path)
            promoted.append(final_path)
        for parent in {path.parent for path in targets}:
            _fsync_directory(parent)
    except Exception:
        for final_path in reversed(promoted):
            final_path.unlink(missing_ok=True)
        for backup, final_path in reversed(backups):
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, final_path)
        raise


def _owned_artifact_paths(
    source_path: Path,
    output_root: Path,
) -> tuple[Path, ...]:
    base = _base_name(source_path)
    production = output_root / BOX_AUTO_ACCEPTED_ROUTE
    review = output_root / "review_required" / base
    return (
        production / f"{base}_自动拆板_清洁1to1.dxf",
        production / f"{base}_自动拆板_报告.json",
        review / f"{base}_复核候选_1to1.dxf",
        review / f"{base}_复核_报告.json",
        review / source_path.name,
    )


def _layout_report(layout: BoxLayout) -> list[dict[str, object]]:
    return [
        {
            "group_id": plate.group_id,
            "roles": [role.value for role in plate.roles],
            "physical_plate_ids": list(plate.physical_plate_ids),
            "quantity": plate.quantity,
            "contract": (
                plate.weld_allowance_contract.to_dict()
                if plate.weld_allowance_contract is not None
                else None
            ),
            "contract_sha256": (
                plate.weld_allowance_contract.summary_sha256
                if plate.weld_allowance_contract is not None
                else None
            ),
        }
        for plate in layout.plates
    ]


def deliver_box_compilation(
    core: BoxCoreCompilation,
    *,
    config: BoxCompileConfig,
    release_attestation: BoxVerifiedReleaseAttestation | None,
) -> BoxDeliveryArtifacts:
    """Write, validate, preview, and atomically promote one frozen BOX MIR."""

    started = perf_counter()
    source_path = core.source.path
    output_root = Path(config.output_dir).resolve()
    disposition = core.proof_report.disposition.value
    if disposition == "rejected":
        raise ValueError("rejected BOX proof cannot generate an output DXF")
    if config.require_auto_accept and disposition != "auto_accept":
        raise ValueError(f"BOX proof disposition is {disposition!r}, not auto_accept")
    if config.require_auto_accept and release_attestation is None:
        raise ValueError(
            "BOX auto-accept requires a release attestation for the current implementation"
        )

    production_ready = (
        disposition == "auto_accept" and release_attestation is not None
    )
    base = _base_name(source_path)
    if production_ready:
        automation_route = BOX_AUTO_ACCEPTED_ROUTE
        route_dir = output_root / BOX_AUTO_ACCEPTED_ROUTE
        production_path = route_dir / f"{base}_自动拆板_清洁1to1.dxf"
        review_path = None
        written_path = production_path
        purpose = OutputPurpose.PRODUCTION
        default_report = route_dir / f"{base}_自动拆板_报告.json"
        source_copy = None
    else:
        automation_route = "review_required"
        route_dir = output_root / "review_required" / base
        production_path = None
        review_path = route_dir / f"{base}_复核候选_1to1.dxf"
        written_path = review_path
        purpose = (
            OutputPurpose.PRODUCTION
            if disposition == "auto_accept"
            else OutputPurpose.REVIEW
        )
        default_report = route_dir / f"{base}_复核_报告.json"
        source_copy = route_dir / source_path.name
    report_path = (
        Path(config.report_path).resolve()
        if config.report_path is not None
        else default_report
    )
    if report_path.drive.casefold() != output_root.drive.casefold():
        raise ValueError("BOX 原子批次要求报告与 DXF 输出位于同一磁盘。")

    output_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".box-v1-stage-", dir=output_root) as temporary:
        stage_root = Path(temporary)
        staged_dxf = stage_root / "output" / written_path.name
        staged_report = stage_root / "report" / report_path.name
        staged_source = (
            stage_root / "source" / source_path.name
            if source_copy is not None
            else None
        )

        layout = write_box_clean(
            core.manufacturing,
            staged_dxf,
            purpose=purpose,
        )
        saved = validate_saved_dxf(
            staged_dxf,
            core.manufacturing,
            layout=layout,
        )
        if saved.get("ok") is not True:
            raise ValueError(
                "BOX saved DXF validation failed: "
                + ", ".join(_failed_checks(saved))
            )

        if staged_source is not None:
            staged_source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, staged_source)

        # Preview PNG rendering is disabled.  The report keeps a stable
        # before/after placeholder so downstream contracts see no artifact.
        preview_seconds = 0.0
        preview_payload = {
            "before": None,
            "after": None,
            "shared_view": False,
        }
        best = core.search.best
        report: dict[str, object] = {
            "version": BOX_CORE_VERSION,
            "report_schema": BOX_COMPILATION_REPORT_SCHEMA,
            "profile_family": "BOX",
            "core": {
                "version": BOX_CORE_VERSION,
                "tag": BOX_CORE_TAG,
                "commit": BOX_CORE_COMMIT,
            },
            "input": str(source_path.resolve()),
            "source_contract": config.source_contract.to_dict(),
            "single_file_disposition": disposition,
            "automation_route": automation_route,
            "non_production_review_candidate": not production_ready,
            "outputs": {
                "production_clean": (
                    str(production_path.resolve())
                    if production_path is not None
                    else None
                ),
                "review_candidate": (
                    str(review_path.resolve()) if review_path is not None else None
                ),
                "source_copy": (
                    str(source_copy.resolve()) if source_copy is not None else None
                ),
                "previews": preview_payload,
            },
            "preview_rendering": {
                **preview_payload,
                "render_seconds": preview_seconds,
            },
            "metadata": {
                "part_number": core.metadata.member_mark.value,
                "profile": core.metadata.profile.value.canonical,
                "material": core.metadata.material.value,
                "nominal_length_mm": core.metadata.nominal_length.value,
            },
            "proof_report": core.proof_report.to_dict(),
            "search_status": {
                "search_complete": core.search.search_complete,
                "hypothesis_count": len(core.search.hypotheses),
                "diagnostics": list(core.search.diagnostics),
                "selected_view_assignment": best.assignment.signature,
                "score_terms": [
                    {
                        "name": term.name,
                        "value": term.value,
                        "description": term.description,
                    }
                    for term in best.score_terms
                ],
            },
            "manufacturing_ir": {
                "fingerprint": core.fingerprint,
                **core.manufacturing.to_dict(),
            },
            "manufacturing_ir_validation": core.validation,
            "saved_dxf": saved,
            "release_attestation": (
                release_attestation.to_dict()
                if release_attestation is not None
                else None
            ),
            "weld_allowance_output_groups": _layout_report(layout),
            "source_fingerprints": {
                "file_sha256": core.source.file_sha256,
                "geometry": core.source.geometry_fingerprint,
            },
            "ground_truth_used_for_decision": False,
            "legacy_solver_called": False,
            "writer": "native_lwpolyline_circle",
            "codegen_purpose": purpose.value,
            "batch_atomicity": {
                "staged_before_promotion": True,
                "all_outputs_validated_before_promotion": True,
                "rollback_on_promotion_failure": True,
            },
            "timing": {
                "clock": "time.perf_counter",
                "preview_render_seconds": preview_seconds,
                "processing_seconds": perf_counter() - started,
            },
        }
        _write_json_staged(staged_report, report)
        staged_pairs = [
            (staged_dxf, written_path),
        ]
        if staged_source is not None and source_copy is not None:
            staged_pairs.append((staged_source, source_copy))
        staged_pairs.append((staged_report, report_path))
        staged_to_final = tuple(staged_pairs)
        current_targets = {final for _, final in staged_to_final}
        stale = tuple(
            path
            for path in _owned_artifact_paths(source_path, output_root)
            if path not in current_targets
        )
        _promote_staged_files(
            staged_to_final,
            backup_dir=stage_root / ".backups",
            remove_paths=stale,
        )

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    return BoxDeliveryArtifacts(
        production_path=production_path,
        review_path=review_path,
        report_path=report_path,
        report=persisted,
    )
