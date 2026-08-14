from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.lldxf.const import DXFValueError

from . import __version__
from .bh_manufacturing_ir import (
    BHContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    WeldAllowanceContract,
    WeldAllowanceContractError,
    derive_weld_allowance_contract,
    weld_allowance_contract_sha256,
)
from .bh_region import MAX_SAGITTA_MM
from .bh_writer import _escape_non_ascii_dxf_text


_XDATA_APPID = "STEEL_DXF_SPLIT"
_XDATA_SCHEMA = "BH-WELD-ALLOWANCE-1.0"
_CUT_XDATA_SCHEMA = "BH-CUT-FEATURE-1.0"
_REPORT_SCHEMA = "BH-WELD-ALLOWANCE-REPORT-1.0"
_REPORT_INPUT_SCHEMA = "BH-COMPILATION-REPORT-1.4"
_GEOMETRY_TOLERANCE_MM = MAX_SAGITTA_MM + 1e-6


class WeldAllowanceProcessingError(ValueError):
    """An already-split DXF is not authorized for allowance processing."""


@dataclass(frozen=True, slots=True)
class AllowanceResult:
    input_path: Path
    output_path: Path
    report_path: Path
    plate_results: tuple[dict[str, object], ...]


def stretch_outer_segments(
    segments: tuple[BHContourSegmentIR, ...],
    contract: WeldAllowanceContract,
) -> tuple[BHContourSegmentIR, ...]:
    """Translate only the proven positive terminal chain along +X."""

    expected = derive_weld_allowance_contract(segments)
    if expected != contract:
        raise WeldAllowanceProcessingError(
            "Weld allowance contract does not match the supplied outer contour."
        )
    if contract.allowance_mm == 0.0:
        return segments
    index_by_id = {
        segment.segment_id: index for index, segment in enumerate(segments)
    }
    try:
        terminal_indices = tuple(
            index_by_id[segment_id]
            for segment_id in contract.positive_terminal_segment_ids
        )
    except KeyError as exc:
        raise WeldAllowanceProcessingError(
            "Weld allowance terminal segment is absent from the contour."
        ) from exc
    movable_vertices = {
        vertex_index
        for segment_index in terminal_indices
        for vertex_index in (segment_index, (segment_index + 1) % len(segments))
    }

    def moved(point: tuple[float, float], vertex_index: int) -> tuple[float, float]:
        if vertex_index not in movable_vertices:
            return point
        return (point[0] + contract.allowance_mm, point[1])

    return tuple(
        replace(
            segment,
            start=moved(segment.start, index),
            end=moved(segment.end, (index + 1) % len(segments)),
        )
        for index, segment in enumerate(segments)
    )


def _load_compilation_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WeldAllowanceProcessingError(
            f"Cannot read the compilation report: {path}."
        ) from exc
    if not isinstance(payload, dict):
        raise WeldAllowanceProcessingError("Compilation report must be a JSON object.")
    if payload.get("version") != __version__:
        raise WeldAllowanceProcessingError(
            "Compilation report version does not match the running compiler."
        )
    if payload.get("report_schema") != _REPORT_INPUT_SCHEMA:
        raise WeldAllowanceProcessingError(
            "Compilation report does not expose the v1.5 allowance contract."
        )
    if payload.get("automation_route") != "production":
        raise WeldAllowanceProcessingError(
            "Only production auto-accepted DXF may receive weld allowance."
        )
    if (payload.get("saved_dxf") or {}).get("ok") is not True:
        raise WeldAllowanceProcessingError(
            "Compilation report has no successful saved-DXF proof."
        )
    if (payload.get("manufacturing_ir_validation") or {}).get("ok") is not True:
        raise WeldAllowanceProcessingError(
            "Compilation report has no valid manufacturing IR proof."
        )
    manufacturing = payload.get("manufacturing_ir")
    if not isinstance(manufacturing, dict):
        raise WeldAllowanceProcessingError(
            "Compilation report is missing manufacturing IR."
        )
    return payload


def _contracts_by_plate_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    manufacturing = report["manufacturing_ir"]
    result: dict[str, dict[str, Any]] = {}
    for plate in manufacturing.get("plates", []):
        if not isinstance(plate, dict):
            continue
        plate_id = plate.get("plate_id")
        contract = plate.get("weld_allowance_contract")
        if isinstance(plate_id, str) and isinstance(contract, dict):
            result[plate_id] = contract
    return result


def _plate_binding(entity) -> dict[str, object]:
    try:
        tags = list(entity.get_xdata(_XDATA_APPID))
    except DXFValueError as exc:
        raise WeldAllowanceProcessingError(
            "PLATE_CUT closed polyline is missing its weld allowance XDATA binding."
        ) from exc
    if [tag.code for tag in tags] != [1000, 1000, 1000, 1000, 1040, 1040, 1000, 1000]:
        raise WeldAllowanceProcessingError(
            "PLATE_CUT closed polyline has an invalid weld allowance XDATA layout."
        )
    values = [tag.value for tag in tags]
    if values[0] != _XDATA_SCHEMA or values[3] != "mm":
        raise WeldAllowanceProcessingError(
            "PLATE_CUT closed polyline has an incompatible allowance schema or unit."
        )
    return {
        "plate_id": str(values[1]),
        "role": str(values[2]),
        "coordinate_unit": str(values[3]),
        "main_length_mm": float(values[4]),
        "allowance_mm": float(values[5]),
        "contract_sha256": str(values[6]),
        "manufacturing_ir_fingerprint": str(values[7]),
    }


def _cut_binding(entity) -> tuple[str, str]:
    try:
        tags = list(entity.get_xdata(_XDATA_APPID))
    except DXFValueError as exc:
        raise WeldAllowanceProcessingError(
            "CUT_HOLE circle is missing its manufacturing feature binding."
        ) from exc
    if [tag.code for tag in tags] != [1000, 1000, 1000]:
        raise WeldAllowanceProcessingError(
            "CUT_HOLE circle has an invalid manufacturing feature binding."
        )
    values = [str(tag.value) for tag in tags]
    if values[0] != _CUT_XDATA_SCHEMA:
        raise WeldAllowanceProcessingError(
            "CUT_HOLE circle has an incompatible manufacturing feature schema."
        )
    return values[1], values[2]


def _bound_cuts(document: ezdxf.document.Drawing) -> dict[str, object]:
    result: dict[str, object] = {}
    for entity in document.modelspace().query("CIRCLE[layer=='CUT_HOLE']"):
        _, cut_id = _cut_binding(entity)
        if cut_id in result:
            raise WeldAllowanceProcessingError(
                f"Duplicate circular-cut binding in input DXF: {cut_id}."
            )
        result[cut_id] = entity
    return result


def _boundary_segments(entity, plate_id: str) -> tuple[BHContourSegmentIR, ...]:
    if not entity.closed:
        raise WeldAllowanceProcessingError(
            f"Plate boundary is not closed for {plate_id}."
        )
    vertices = tuple(
        (float(point[0]), float(point[1]), float(point[2]))
        for point in entity.get_points("xyb")
    )
    if len(vertices) < 3:
        raise WeldAllowanceProcessingError(
            f"Plate boundary has fewer than three vertices for {plate_id}."
        )
    evidence = FeatureEvidence(
        state=EvidenceState.DIRECT,
        source_ids=(plate_id,),
        rule_ids=("BH.RULE.WELD_ALLOWANCE.NATIVE_POLYLINE_ROUND_TRIP",),
        proof_ids=("BH.PROOF.WELD_ALLOWANCE.INPUT_BINDING",),
    )
    return tuple(
        BHContourSegmentIR(
            segment_id=f"{plate_id}:saved-polyline:{index:04d}",
            start=(point[0], point[1]),
            end=(vertices[(index + 1) % len(vertices)][0], vertices[(index + 1) % len(vertices)][1]),
            bulge=point[2],
            evidence=evidence,
        )
        for index, point in enumerate(vertices)
    )


def _cut_geometry(document: ezdxf.document.Drawing) -> tuple[tuple[object, ...], ...]:
    curves: list[tuple[object, ...]] = []
    for entity in document.modelspace().query("LWPOLYLINE[layer=='CUT_HOLE']"):
        curves.append(
            (
                "LWPOLYLINE",
                entity.closed,
                int(entity.dxf.color),
                *tuple(tuple(point) for point in entity.get_points("xyb")),
            )
        )
    for entity in document.modelspace().query("CIRCLE[layer=='CUT_HOLE']"):
        curves.append(
            (
                "CIRCLE",
                tuple(float(value) for value in entity.dxf.center),
                float(entity.dxf.radius),
                int(entity.dxf.color),
            )
        )
    return tuple(curves)


def _verify_cut_feature_contract(
    before: ezdxf.document.Drawing,
    after: ezdxf.document.Drawing,
    plate_results: tuple[dict[str, object], ...],
) -> bool:
    before_bound = _bound_cuts(before)
    after_bound = _bound_cuts(after)
    if set(before_bound) != set(after_bound):
        return False
    translations = {
        str(cut_id): float(result["allowance_mm"])
        for result in plate_results
        for cut_id in result.get("positive_terminal_cut_ids", [])
    }
    for cut_id, before_entity in before_bound.items():
        after_entity = after_bound[cut_id]
        if (
            int(before_entity.dxf.color) != int(after_entity.dxf.color)
            or not math.isclose(
                float(before_entity.dxf.radius),
                float(after_entity.dxf.radius),
                abs_tol=1e-9,
            )
            or not math.isclose(
                float(after_entity.dxf.center.x),
                float(before_entity.dxf.center.x) + translations.get(cut_id, 0.0),
                abs_tol=1e-6,
            )
            or not math.isclose(
                float(after_entity.dxf.center.y),
                float(before_entity.dxf.center.y),
                abs_tol=1e-6,
            )
        ):
            return False
    before_inner = tuple(
        item
        for item in _cut_geometry(before)
        if item[0] == "LWPOLYLINE"
    )
    after_inner = tuple(
        item
        for item in _cut_geometry(after)
        if item[0] == "LWPOLYLINE"
    )
    return before_inner == after_inner


def _labels(document: ezdxf.document.Drawing) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            entity.dxf.text,
            tuple(float(value) for value in entity.dxf.insert),
            float(entity.dxf.height),
            entity.dxf.style,
            entity.dxf.layer,
        )
        for entity in document.modelspace().query("TEXT")
    )


def _entity_counts(document: ezdxf.document.Drawing) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for entity in document.modelspace():
        key = (entity.dxftype(), entity.dxf.layer)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _validate_and_transform(
    document: ezdxf.document.Drawing,
    report: dict[str, Any],
) -> tuple[dict[str, object], ...]:
    if document.dxfversion != "AC1021" or int(document.header.get("$INSUNITS", 0)) != 4:
        raise WeldAllowanceProcessingError(
            "Weld allowance input must be an R2007 1:1 millimetre DXF."
        )
    manufacturing = report["manufacturing_ir"]
    fingerprint = manufacturing.get("fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise WeldAllowanceProcessingError(
            "Compilation report has no valid manufacturing fingerprint."
        )
    contracts = _contracts_by_plate_id(report)
    bound_cuts = _bound_cuts(document)
    plates = list(document.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']"))
    if not plates:
        raise WeldAllowanceProcessingError("Input DXF has no plate closed polyline.")
    results: list[dict[str, object]] = []
    seen: set[str] = set()
    for entity in plates:
        binding = _plate_binding(entity)
        plate_id = str(binding["plate_id"])
        if plate_id in seen:
            raise WeldAllowanceProcessingError(
                f"Duplicate plate binding in input DXF: {plate_id}."
            )
        seen.add(plate_id)
        if binding["manufacturing_ir_fingerprint"] != fingerprint:
            raise WeldAllowanceProcessingError(
                f"Manufacturing fingerprint mismatch for plate {plate_id}."
            )
        source_contract = contracts.get(plate_id)
        if source_contract is None:
            raise WeldAllowanceProcessingError(
                f"Compilation report has no allowance contract for plate {plate_id}."
            )
        if weld_allowance_contract_sha256(source_contract) != binding["contract_sha256"]:
            raise WeldAllowanceProcessingError(
                f"Allowance contract digest mismatch for plate {plate_id}."
            )
        if source_contract.get("coordinate_unit") != "mm":
            raise WeldAllowanceProcessingError(
                f"Allowance contract is not in millimetres for plate {plate_id}."
            )
        before_segments = _boundary_segments(entity, plate_id)
        try:
            actual_contract = derive_weld_allowance_contract(before_segments)
        except WeldAllowanceContractError as exc:
            raise WeldAllowanceProcessingError(
                f"Saved plate closed polyline has no unique positive terminal: {plate_id}."
            ) from exc
        declared_length = float(binding["main_length_mm"])
        declared_allowance = float(binding["allowance_mm"])
        if (
            not math.isclose(
                actual_contract.main_length_mm,
                declared_length,
                abs_tol=_GEOMETRY_TOLERANCE_MM,
            )
            or not math.isclose(
                float(source_contract.get("main_length_mm", math.nan)),
                declared_length,
                abs_tol=1e-6,
            )
            or actual_contract.allowance_mm != declared_allowance
            or float(source_contract.get("allowance_mm", math.nan))
            != declared_allowance
        ):
            raise WeldAllowanceProcessingError(
                f"Saved geometry and allowance length contract disagree for plate {plate_id}."
            )
        after_segments = stretch_outer_segments(before_segments, actual_contract)
        if declared_allowance > 0.0:
            entity.set_points(
                [
                    (segment.start[0], segment.start[1], segment.bulge)
                    for segment in after_segments
                ],
                format="xyb",
            )
        moving_cut_ids = tuple(
            map(str, source_contract.get("positive_terminal_cut_ids", ()))
        )
        declared_cut_ids = {
            str(cut.get("cut_id"))
            for plate in manufacturing.get("plates", [])
            if isinstance(plate, dict) and plate.get("plate_id") == plate_id
            for cut in plate.get("circular_cuts", [])
            if isinstance(cut, dict) and isinstance(cut.get("cut_id"), str)
        }
        if not set(moving_cut_ids) <= declared_cut_ids:
            raise WeldAllowanceProcessingError(
                f"Allowance contract names an unknown circular cut for plate {plate_id}."
            )
        for cut_id in moving_cut_ids:
            cut_entity = bound_cuts.get(cut_id)
            if cut_entity is None:
                raise WeldAllowanceProcessingError(
                    f"Allowance cut binding is absent from the DXF: {cut_id}."
                )
            bound_plate_id, _ = _cut_binding(cut_entity)
            if bound_plate_id != plate_id:
                raise WeldAllowanceProcessingError(
                    f"Allowance cut is bound to the wrong plate: {cut_id}."
                )
            cut_entity.dxf.center = (
                float(cut_entity.dxf.center.x) + declared_allowance,
                float(cut_entity.dxf.center.y),
                float(cut_entity.dxf.center.z),
            )
        results.append(
            {
                "plate_id": plate_id,
                "role": binding["role"],
                "coordinate_unit": "mm",
                "before_main_length_mm": declared_length,
                "allowance_mm": declared_allowance,
                "after_main_length_mm": declared_length + declared_allowance,
                "stationary_end": "negative_x",
                "moved_end": "positive_x",
                "terminal_translation_mm": [declared_allowance, 0.0],
                "terminal_inclination_preserved": True,
                "positive_terminal_cut_ids": list(moving_cut_ids),
                "moved_circular_cut_count": len(moving_cut_ids),
                "cuts_follow_declared_feature_contract": True,
            }
        )
    return tuple(results)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def apply_weld_allowance(
    input_path: Path,
    compilation_report_path: Path,
    output_path: Path,
    report_path: Path,
) -> AllowanceResult:
    """Create an independent allowance DXF without modifying split outputs."""

    input_path = Path(input_path)
    compilation_report_path = Path(compilation_report_path)
    output_path = Path(output_path)
    report_path = Path(report_path)
    if input_path.resolve() in {output_path.resolve(), report_path.resolve()}:
        raise WeldAllowanceProcessingError(
            "Weld allowance output must not overwrite its split input."
        )
    report = _load_compilation_report(compilation_report_path)
    try:
        document = ezdxf.readfile(input_path)
    except (OSError, ezdxf.DXFError) as exc:
        raise WeldAllowanceProcessingError(
            f"Cannot read the split production DXF: {input_path}."
        ) from exc
    before_document = ezdxf.readfile(input_path)
    before_labels = _labels(document)
    before_counts = _entity_counts(document)
    plate_results = _validate_and_transform(document, report)
    auditor = document.audit()
    if auditor.has_errors:
        raise WeldAllowanceProcessingError(
            f"Allowance DXF audit failed with {len(auditor.errors)} errors."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    pending_output = output_path.with_name(
        f".{output_path.stem}.pending{output_path.suffix}"
    )
    pending_report = report_path.with_name(f".{report_path.name}.pending")
    pending_output.unlink(missing_ok=True)
    pending_report.unlink(missing_ok=True)
    try:
        document.saveas(pending_output)
        # ezdxf writes a fresh R2007 file here. Reapply the production
        # writer's ASCII DXF-Unicode transport so the independent allowance
        # result cannot reintroduce UTF-8 bytes under a legacy codepage header.
        _escape_non_ascii_dxf_text(pending_output)
        saved = ezdxf.readfile(pending_output)
        saved_audit = saved.audit()
        if saved_audit.has_errors:
            raise WeldAllowanceProcessingError(
                f"Saved allowance DXF audit failed with {len(saved_audit.errors)} errors."
            )
        if int(saved.header.get("$INSUNITS", 0)) != 4:
            raise WeldAllowanceProcessingError(
                "Saved allowance DXF lost its millimetre unit declaration."
            )
        if not _verify_cut_feature_contract(
            before_document,
            saved,
            plate_results,
        ):
            raise WeldAllowanceProcessingError(
                "A circular cut or inner contour violated its allowance feature contract."
            )
        if _labels(saved) != before_labels:
            raise WeldAllowanceProcessingError(
                "A plate label changed during allowance processing."
            )
        if _entity_counts(saved) != before_counts:
            raise WeldAllowanceProcessingError(
                "Entity or layer counts changed during allowance processing."
            )
        saved_plates = list(
            saved.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")
        )
        if len(saved_plates) != len(plate_results):
            raise WeldAllowanceProcessingError(
                "Saved allowance DXF changed the plate closed-polyline count."
            )
        for entity, result in zip(saved_plates, plate_results, strict=True):
            binding = _plate_binding(entity)
            boundary = tuple(entity.get_points("xyb"))
            actual_length = (
                max(float(point[0]) for point in boundary)
                - min(float(point[0]) for point in boundary)
            )
            if binding["plate_id"] != result["plate_id"] or not math.isclose(
                actual_length,
                float(result["after_main_length_mm"]),
                abs_tol=_GEOMETRY_TOLERANCE_MM,
            ):
                raise WeldAllowanceProcessingError(
                    "Saved allowance plate failed its main-length proof."
                )
        payload: dict[str, object] = {
            "schema": _REPORT_SCHEMA,
            "version": __version__,
            "ok": True,
            "coordinate_unit": "mm",
            "input_split_dxf": str(input_path.resolve()),
            "input_compilation_report": str(
                compilation_report_path.resolve()
            ),
            "output_dxf": str(output_path.resolve()),
            "png_generated": False,
            "original_split_result_preserved": True,
            "plates": list(plate_results),
            "checks": {
                "input_is_r2007_millimetres": True,
                "native_curve_contracts_match_report": True,
                "positive_terminal_rigid_translation": True,
                "terminal_inclinations_preserved": True,
                "cut_hole_feature_contracts_match": True,
                "inner_contours_unchanged": True,
                "labels_unchanged": True,
                "entity_and_layer_counts_unchanged": True,
                "saved_dxf_audit_clean": True,
                "png_not_generated": True,
            },
        }
        _write_json(pending_report, payload)
        pending_output.replace(output_path)
        pending_report.replace(report_path)
    finally:
        pending_output.unlink(missing_ok=True)
        pending_report.unlink(missing_ok=True)
    return AllowanceResult(
        input_path=input_path,
        output_path=output_path,
        report_path=report_path,
        plate_results=plate_results,
    )
