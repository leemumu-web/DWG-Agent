from __future__ import annotations

import gc
import json
import os
from pathlib import Path
import shutil

from . import __version__
from .bh_compare import compare_bh_to_manual
from .bh_compiler import BHCompilationRejected, compile_bh_document
from .bh_errors import BHDomainError
from .bh_knowledge import BHSourceContract
from .bh_validator import validate_bh_assembly, validate_bh_saved_dxf
from .bh_writer import OutputPurpose, write_bh_clean
from .dxf_io import load_document


def _light_report_enabled() -> bool:
    """Light-weight audit report: skip the full source IR dump.

    The complete per-entity ``source_ir`` makes a report tens of MB and slows
    every drawing.  Set ``DWG_AGENT_LIGHT_REPORT=1`` to keep the audit fields
    (manufacturing IR, proofs, diagnostics, fingerprints) while replacing the
    per-entity dump with a compact summary.
    """
    return os.environ.get("DWG_AGENT_LIGHT_REPORT") == "1"


def _report_source_ir(source_ir) -> dict[str, object] | None:
    if not _light_report_enabled():
        return source_ir.to_dict()
    return {
        "summary_only": True,
        "dxf_version": source_ir.dxf_version,
        "encoding": source_ir.encoding,
        "units": source_ir.units,
        "entity_count": len(source_ir.entities),
        "container_count": len(source_ir.containers),
        "audit_error_count": len(source_ir.audit_errors),
    }


def _base_name(input_path: Path) -> str:
    return input_path.stem.replace("_拆板前", "").replace("拆板前", "").rstrip("_- ")


def _route_directory(output_dir: Path, base: str, route: str) -> Path:
    if route == "production":
        destination = output_dir / "auto_accepted"
    elif route == "review_required":
        destination = output_dir / "review_required" / base
    elif route == "rejected":
        destination = output_dir / "unprocessable" / base
    else:
        raise ValueError(f"Unknown physical output route: {route}")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _copy_source(input_path: Path, destination: Path) -> Path:
    copied = destination / input_path.name
    shutil.copy2(input_path, copied)
    return copied


def _empty_preview_outputs() -> dict[str, object]:
    """Represent routes that have no output DXF and therefore no PNG pair."""

    return {"before": None, "after": None, "shared_view": False}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _capabilities() -> dict[str, object]:
    return {
        "production_member_axis": "horizontal_x",
        "rotation_support": "diagnostic_only",
        "strict_representation_invariants": [
            "translation",
            "mirror_x",
            "mirror_y",
            "insert_nesting_and_explode",
        ],
        "automation_policy": "all applicable critical proofs must pass",
        "ground_truth_used_for_decision": False,
        "review_policy": "missing critical evidence is physically quarantined",
        "rejection_policy": "conflict, incomplete proof or incomplete search",
    }


def _rejected_report(
    input_path: Path,
    output_dir: Path,
    base: str,
    error: BHDomainError,
    *,
    report_path: Path | None,
    require_auto_accept: bool,
) -> tuple[None, None, Path, dict[str, object]]:
    route_dir = _route_directory(output_dir, base, "rejected")
    source_copy = _copy_source(input_path, route_dir)
    proof = (
        error.proof_report.to_dict()
        if isinstance(error, BHCompilationRejected)
        else None
    )
    diagnostic_codes = (
        list(error.diagnostic_codes)
        if isinstance(error, BHCompilationRejected)
        else [error.diagnostic_code]
    )
    destination = report_path or route_dir / f"{base}_隔离_报告.json"
    source_ir = (
        _report_source_ir(error.source_ir)
        if isinstance(error, BHCompilationRejected)
        and error.source_ir is not None
        else None
    )
    canonical_frames = (
        error.frame_result.to_dict()
        if isinstance(error, BHCompilationRejected)
        and error.frame_result is not None
        else None
    )
    drawing_graph = (
        error.drawing_graph.to_dict()
        if isinstance(error, BHCompilationRejected)
        and error.drawing_graph is not None
        else None
    )
    hypotheses = (
        error.hypotheses
        if isinstance(error, BHCompilationRejected)
        else None
    )
    manufacturing = (
        error.manufacturing_ir
        if isinstance(error, BHCompilationRejected)
        else None
    )
    manufacturing_validation = (
        error.manufacturing_validation
        if isinstance(error, BHCompilationRejected)
        else None
    )
    report: dict[str, object] = {
        "version": __version__,
        "report_schema": "BH-COMPILATION-REPORT-1.4",
        "profile_family": "BH",
        "input": str(input_path.resolve()),
        "automation_route": "rejected",
        "require_auto_accept": require_auto_accept,
        "outputs": {
            "production_clean": None,
            "review_candidate": None,
            "source_copy": str(source_copy.resolve()),
            "previews": _empty_preview_outputs(),
        },
        "automation_assessment": {
            "disposition": "rejected",
            "blocking_obligation_ids": (
                list(error.proof_report.blocking_obligation_ids)
                if isinstance(error, BHCompilationRejected)
                else []
            ),
        },
        "proof_report": proof,
        "source_ir": source_ir,
        "canonical_frames": canonical_frames,
        "drawing_graph": drawing_graph,
        "search_status": {
            "search_complete": (
                hypotheses.search_complete
                if hypotheses is not None
                else error.proof_report.search_complete
                if isinstance(error, BHCompilationRejected)
                else False
            ),
            "generated_candidate_count": (
                hypotheses.generated_candidate_count
                if hypotheses is not None
                else None
            ),
            "evaluated_candidate_count": (
                hypotheses.evaluated_candidate_count
                if hypotheses is not None
                else None
            ),
            "pruned_candidate_count": (
                hypotheses.pruned_candidate_count
                if hypotheses is not None
                else None
            ),
            "termination_reason": (
                hypotheses.termination_reason
                if hypotheses is not None
                else None
            ),
        },
        "manufacturing_ir": (
            {"fingerprint": manufacturing.fingerprint, **manufacturing.to_dict()}
            if manufacturing is not None
            else None
        ),
        "manufacturing_ir_validation": (
            manufacturing_validation.to_dict()
            if manufacturing_validation is not None
            else None
        ),
        "source_information_ledger": (
            dict(error.information_ledger)
            if isinstance(error, BHCompilationRejected)
            and error.information_ledger is not None
            else None
        ),
        "semantic_fingerprints": (
            dict(error.fingerprints)
            if isinstance(error, BHCompilationRejected)
            and error.fingerprints is not None
            else None
        ),
        "capabilities": _capabilities(),
        "diagnostic_codes": diagnostic_codes,
        "compilation_error": {
            "type": type(error).__name__,
            "message": str(error),
        },
    }
    _write_json(destination, report)
    return None, None, destination, report


def split_bh_dxf(
    input_path: Path,
    output_dir: Path,
    *,
    source_contract: BHSourceContract,
    manual_reference_path: Path | None = None,
    report_path: Path | None = None,
    require_auto_accept: bool = False,
) -> tuple[Path | None, Path | None, Path, dict[str, object]]:
    """Compile one BH drawing and route it by proof disposition.

    Only ``auto_accept`` can create a production clean DXF.  Auto-accepted,
    review-required and unprocessable inputs use separate physical directories
    so a downstream batch job cannot confuse their manufacturing authority.
    """

    base = _base_name(input_path)
    source_doc = load_document(input_path)
    try:
        compiled = compile_bh_document(
            source_doc,
            source_contract=source_contract,
            source_path=input_path,
        )
    except BHCompilationRejected as error:
        source_doc = None
        gc.collect()
        return _rejected_report(
            input_path,
            output_dir,
            base,
            error,
            report_path=report_path,
            require_auto_accept=require_auto_accept,
        )
    except BHDomainError as error:
        # Only classified engineering failures become auditable rejections.
        # Plain ValueError/TypeError/AssertionError defects must fail the job.
        source_doc = None
        gc.collect()
        return _rejected_report(
            input_path,
            output_dir,
            base,
            error,
            report_path=report_path,
            require_auto_accept=require_auto_accept,
        )
    source_doc = None
    gc.collect()

    assembly = compiled.assembly
    validation = validate_bh_assembly(assembly)
    if not validation.ok:
        failed = [name for name, ok in validation.checks.items() if not ok]
        raise ValueError(f"BH pre-write validation failed: {', '.join(failed)}")

    disposition = compiled.assessment.disposition.value
    route = "production" if disposition == "auto_accept" else "review_required"
    if route == "production" and not compiled.manufacturing_validation.ok:
        raise ValueError(
            "BH production routing requires a valid evidence-backed manufacturing IR."
        )
    route_dir = _route_directory(output_dir, base, route)
    production_path = (
        route_dir / f"{base}_自动拆板_清洁1to1.dxf"
        if route == "production"
        else None
    )
    review_candidate = (
        route_dir / f"{base}_复核候选_1to1.dxf"
        if route == "review_required"
        else None
    )
    written_path = production_path or review_candidate
    assert written_path is not None
    pending_path = written_path.with_name(
        f".{written_path.stem}.pending{written_path.suffix}"
    )
    try:
        purpose = (
            OutputPurpose.PRODUCTION
            if route == "production"
            else OutputPurpose.REVIEW
        )
        layout = write_bh_clean(
            compiled.manufacturing_ir,
            pending_path,
            purpose=purpose,
        )
        saved = validate_bh_saved_dxf(
            pending_path,
            compiled.manufacturing_ir,
            layout=layout,
        )
        if not saved["ok"]:
            failed = [name for name, ok in saved["checks"].items() if not ok]
            raise ValueError(
                f"BH saved DXF validation failed: {', '.join(failed)}"
            )
        pending_path.replace(written_path)
    finally:
        pending_path.unlink(missing_ok=True)

    # Preview PNG rendering is disabled.  The report keeps a stable
    # before/after placeholder so downstream contracts see no artifact.
    preview_render_seconds = 0.0
    preview_outputs = _empty_preview_outputs()

    source_copy = (
        _copy_source(input_path, route_dir)
        if route == "review_required"
        else None
    )
    # Never discover a sibling 拆板后 drawing.  Only an explicitly supplied
    # offline reference may be read, and only after compilation/routing freeze.
    reference = manual_reference_path
    comparison = None
    if reference is not None:
        if not reference.exists():
            raise FileNotFoundError(reference)
        comparison_obj = compare_bh_to_manual(assembly, reference)
        comparison = comparison_obj.to_dict()

    destination = report_path or route_dir / (
        f"{base}_自动拆板_报告.json"
        if route == "production"
        else f"{base}_复核_报告.json"
    )
    report: dict[str, object] = {
        "version": __version__,
        "report_schema": "BH-COMPILATION-REPORT-1.4",
        "profile_family": "BH",
        "input": str(input_path.resolve()),
        "manual_reference": str(reference.resolve()) if reference else None,
        "automation_route": route,
        "require_auto_accept": require_auto_accept,
        "outputs": {
            "production_clean": (
                str(production_path.resolve()) if production_path else None
            ),
            "review_candidate": (
                str(review_candidate.resolve()) if review_candidate else None
            ),
            "source_copy": str(source_copy.resolve()) if source_copy else None,
            "previews": preview_outputs,
        },
        "preview_rendering": {
            "schema": None,
            "renderer": None,
            "shared_view": False,
            "view_bounds": None,
            "canvas_pixels": None,
            "dpi": None,
            "font_fallback": None,
            "render_seconds": preview_render_seconds,
        },
        "naming_policy": {
            "web": "腹板",
            "identical_flanges": "翼缘板×2",
            "different_flanges": "翼缘板-1 / 翼缘板-2",
            "manual_aliases_accepted": ["腹", "翼", "翼-1", "翼-2"],
        },
        "cross_line_policy": {
            "bolt_circle_is_cut": True,
            "bolt_line_is_helper": True,
            "raw_bolt_lines_copied": False,
            "generated_line_xline_ray_allowed": False,
        },
        "validation": validation.to_dict(),
        "saved_dxf": saved,
        "supervised_comparison": comparison,
        "supervised_comparison_used_for_decision": False,
        "compiler": assembly.diagnostics.get("compiler_trace"),
        "source_information_ledger": compiled.information_ledger,
        "knowledge_base": assembly.diagnostics.get("knowledge_base"),
        "hypothesis_solver": assembly.diagnostics.get("hypothesis_solver"),
        "automation_assessment": assembly.diagnostics.get("automation_assessment"),
        "proof_report": compiled.proof_report.to_dict(),
        "source_ir": _report_source_ir(compiled.source_ir),
        "canonical_frames": compiled.frame_result.to_dict(),
        "drawing_graph": compiled.drawing_graph.to_dict(),
        "search_status": {
            "search_complete": compiled.hypotheses.search_complete,
            "generated_candidate_count": (
                compiled.hypotheses.generated_candidate_count
            ),
            "evaluated_candidate_count": (
                compiled.hypotheses.evaluated_candidate_count
            ),
            "pruned_candidate_count": compiled.hypotheses.pruned_candidate_count,
            "termination_reason": compiled.hypotheses.termination_reason,
        },
        "manufacturing_ir": {
            "fingerprint": compiled.manufacturing_ir.fingerprint,
            **compiled.manufacturing_ir.to_dict(),
        },
        "manufacturing_ir_validation": (
            compiled.manufacturing_validation.to_dict()
        ),
        "capabilities": _capabilities(),
        "diagnostic_codes": [
            item.diagnostic_code
            for item in compiled.proof_report.obligations
            if item.diagnostic_code is not None
        ],
        "semantic_fingerprints": compiled.fingerprints,
        "diagnostics": assembly.diagnostics,
    }
    _write_json(destination, report)
    return production_path, review_candidate, destination, report
