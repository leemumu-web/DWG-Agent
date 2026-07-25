from __future__ import annotations

import gc
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ezdxf.entities import DXFEntity

from .bh_compare import compare_bh_to_manual
from .bh_compiler import compile_bh_document
from .bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from .bh_trace import STAGE_REGISTRY, InMemoryTraceObserver, TraceEvent
from .bh_trace_geometry import entity_shapes
from .bh_validator import validate_bh_saved_dxf
from .bh_writer import OutputPurpose, write_bh_clean
from .dxf_io import load_document, recursive_virtual_entities
from .layered_artifacts import (
    ArchiveValidationReport,
    ArtifactItem,
    LayeredArchive,
)
from .layered_scene import scene_from_event


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _geometry_entities(doc) -> list[DXFEntity]:
    result: list[DXFEntity] = []
    supported = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "ELLIPSE", "SPLINE"}
    for entity in doc.modelspace():
        entities = recursive_virtual_entities(entity) if entity.dxftype() == "INSERT" else [entity]
        result.extend(item for item in entities if item.dxftype() in supported)
    return result


def _stage_status(events: tuple[TraceEvent, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for stage in STAGE_REGISTRY:
        if stage.stage_id == "13_corpus_summary":
            continue
        statuses = [event.status for event in events if event.stage_id == stage.stage_id]
        if any(status in {"observed", "selected"} for status in statuses):
            result[stage.stage_id] = "observed"
        elif "failed" in statuses:
            result[stage.stage_id] = "failed"
        else:
            result[stage.stage_id] = "not_applicable"
    return result


@dataclass(frozen=True, slots=True)
class LayeredPairResult:
    sample_id: str
    ok: bool
    artifacts: tuple[ArtifactItem, ...]
    stage_status: dict[str, str]
    supervision: dict[str, Any]
    proof_disposition: str
    output_purpose: str
    supervision_gate_applicable: bool
    supervision_gate_passed: bool | None
    saved_validation: dict[str, Any]
    manifest_validation: ArchiveValidationReport
    final_dxf_path: Path
    final_svg_path: Path
    reference_dxf_path: Path
    reference_svg_path: Path
    comparison_dxf_path: Path
    comparison_svg_path: Path
    manufacturing_fingerprint: str
    selected_hypothesis: str

    def all_paths(self) -> tuple[Path, ...]:
        paths = {
            self.final_dxf_path,
            self.final_svg_path,
            self.reference_dxf_path,
            self.reference_svg_path,
            self.comparison_dxf_path,
            self.comparison_svg_path,
        }
        for item in self.artifacts:
            paths.update({item.dxf_path, item.svg_path})
            if item.json_path is not None:
                paths.add(item.json_path)
        return tuple(sorted(paths, key=lambda path: path.as_posix()))

    def to_manifest(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "ok": self.ok,
            "stage_status": dict(self.stage_status),
            "supervision": self.supervision,
            "proof_disposition": self.proof_disposition,
            "output_purpose": self.output_purpose,
            "supervision_gate_applicable": self.supervision_gate_applicable,
            "supervision_gate_passed": self.supervision_gate_passed,
            "saved_validation": self.saved_validation,
            "manifest_validation": self.manifest_validation.to_dict(),
            "final_dxf_path": self.final_dxf_path.as_posix(),
            "final_svg_path": self.final_svg_path.as_posix(),
            "reference_dxf_path": self.reference_dxf_path.as_posix(),
            "reference_svg_path": self.reference_svg_path.as_posix(),
            "comparison_dxf_path": self.comparison_dxf_path.as_posix(),
            "comparison_svg_path": self.comparison_svg_path.as_posix(),
            "manufacturing_fingerprint": self.manufacturing_fingerprint,
            "selected_hypothesis": self.selected_hypothesis,
            "artifacts": [item.to_dict() for item in self.artifacts],
        }


def inspect_bh_pair(
    input_path: Path,
    manual_reference_path: Path,
    output_root: Path,
) -> LayeredPairResult:
    input_path = Path(input_path)
    manual_reference_path = Path(manual_reference_path)
    output_root = Path(output_root)
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if not manual_reference_path.exists():
        raise FileNotFoundError(manual_reference_path)
    output_root.mkdir(parents=True, exist_ok=True)
    sample_id = input_path.name.removesuffix("_拆板前.dxf")
    if not sample_id or sample_id == input_path.name:
        raise ValueError(f"Input is not a *_拆板前.dxf file: {input_path.name}")

    observer = InMemoryTraceObserver(sample_id)
    source_doc = load_document(input_path)
    source_entities = _geometry_entities(source_doc)
    observer.emit(
        stage_id="00_input_provenance",
        artifact_id="input_provenance",
        status="observed",
        title_zh="输入与来源",
        summary_zh="仅记录自动拆板输入的名称、哈希与源图几何。",
        shapes=entity_shapes("source", source_entities),
        payload={
            "source_name": input_path.name,
            "source_sha256": _file_sha256(input_path),
            "source_bytes": input_path.stat().st_size,
            "source_dxf_version": source_doc.dxfversion,
            "source_units": int(source_doc.header.get("$INSUNITS", 0)),
            "source_geometry_entity_count": len(source_entities),
        },
    )

    compile_result = compile_bh_document(
        source_doc,
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=Path(input_path.name),
        observer=observer,
    )
    assembly = compile_result.assembly
    selected_hypothesis = compile_result.hypotheses.selected.hypothesis_id
    proof_disposition = compile_result.manufacturing_ir.proof_disposition
    output_purpose = (
        OutputPurpose.PRODUCTION
        if proof_disposition == "auto_accept"
        else OutputPurpose.REVIEW
    )
    supervision_gate_applicable = proof_disposition == "auto_accept"
    del source_doc, source_entities
    gc.collect()

    # The manufacturing interpretation and route are now frozen.  Only this
    # offline stage may read manual-reference bytes.
    manual_reference_sha256 = _file_sha256(manual_reference_path)
    manual_reference_bytes = manual_reference_path.stat().st_size

    with TemporaryDirectory(prefix=f"{sample_id}-layered-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        final_source = temporary_root / (
            f"{sample_id}_自动拆板_清洁1to1.dxf"
            if output_purpose == OutputPurpose.PRODUCTION
            else f"{sample_id}_复核候选.dxf"
        )
        layout = write_bh_clean(
            compile_result.manufacturing_ir,
            final_source,
            purpose=output_purpose,
            observer=observer,
            hypothesis_id=selected_hypothesis,
        )
        saved_validation = validate_bh_saved_dxf(
            final_source,
            compile_result.manufacturing_ir,
            layout=layout,
        )
        layout_event = next(
            event
            for event in reversed(observer.events)
            if event.artifact_id == "codegen_layout"
        )
        observer.emit(
            stage_id="11_saved_output_validation",
            artifact_id="saved_output_validation",
            status="observed" if saved_validation["ok"] else "failed",
            title_zh="保存后 DXF 验证",
            summary_zh=(
                "最终 DXF 重读、实体计数与制造安全检查全部通过。"
                if saved_validation["ok"]
                else "最终 DXF 至少一项保存后检查失败。"
            ),
            hypothesis_id=selected_hypothesis,
            shapes=layout_event.shapes,
            payload={
                **saved_validation,
                "output_name": final_source.name,
                "layout_plate_count": len(layout.plates),
            },
        )

        comparison = compare_bh_to_manual(
            assembly,
            manual_reference_path,
            observer=observer,
            hypothesis_id=selected_hypothesis,
        )
        supervision = comparison.to_dict()
        supervision["values"] = {
            **supervision["values"],
            "manual_reference": manual_reference_path.name,
        }
        supervision_gate_passed = (
            comparison.ok if supervision_gate_applicable else None
        )
        supervision.update(
            {
                "manual_reference_sha256": manual_reference_sha256,
                "manual_reference_bytes": manual_reference_bytes,
                "manual_read_phase": "after_manufacturing_ir_freeze",
                "used_for_decision": False,
                "gate_applicable": supervision_gate_applicable,
                "gate_passed": supervision_gate_passed,
            }
        )

        for stage in STAGE_REGISTRY:
            if stage.stage_id == "13_corpus_summary":
                continue
            if any(event.stage_id == stage.stage_id for event in observer.events):
                continue
            observer.emit(
                stage_id=stage.stage_id,
                artifact_id="stage_not_applicable",
                status="not_applicable",
                title_zh=stage.title_zh,
                summary_zh="该样本在现有算法路径中没有触发此阶段的专用产物。",
                payload={"reason": "no_authoritative_event_for_sample"},
            )

        archive = LayeredArchive(output_root)
        for event in observer.events:
            archive.write_event(event)

        final_scene = replace(
            scene_from_event(layout_event),
            warning=(
                "FINAL MANUFACTURING OUTPUT / 最终生产下料"
                if output_purpose == OutputPurpose.PRODUCTION
                else "REVIEW CANDIDATE / 仅供工程复核"
            ),
        )
        final_item = archive.write_scene_pair(
            final_scene,
            category="final",
            dxf_source=final_source,
            filename=final_source.stem,
        )
        reference_event = next(
            event
            for event in observer.events
            if event.artifact_id == "manual_selected_plates"
        )
        reference_scene = replace(
            scene_from_event(reference_event),
            warning="MANUAL REFERENCE / 人工拆板参考",
        )
        reference_item = archive.write_scene_pair(
            reference_scene,
            category="reference",
            dxf_source=manual_reference_path,
            filename=f"{sample_id}_人工拆板",
        )
        comparison_event = next(
            event
            for event in observer.events
            if event.artifact_id == "manual_overlay"
        )
        comparison_scene = replace(
            scene_from_event(comparison_event),
            warning="SUPERVISION COMPARISON / 自动人工核验",
        )
        comparison_item = archive.write_scene_pair(
            comparison_scene,
            category="comparison",
            filename=f"{sample_id}_自动人工叠加",
        )

    manifest_validation = archive.validate()
    stage_status = _stage_status(tuple(observer.events))
    fingerprint = compile_result.fingerprints["manufacturing_ir"]
    ok = bool(
        saved_validation["ok"]
        and manifest_validation.ok
        and (comparison.ok if supervision_gate_applicable else True)
    )
    return LayeredPairResult(
        sample_id=sample_id,
        ok=ok,
        artifacts=tuple(archive.items),
        stage_status=stage_status,
        supervision=supervision,
        proof_disposition=proof_disposition,
        output_purpose=output_purpose.value,
        supervision_gate_applicable=supervision_gate_applicable,
        supervision_gate_passed=supervision_gate_passed,
        saved_validation=saved_validation,
        manifest_validation=manifest_validation,
        final_dxf_path=final_item.dxf_path,
        final_svg_path=final_item.svg_path,
        reference_dxf_path=reference_item.dxf_path,
        reference_svg_path=reference_item.svg_path,
        comparison_dxf_path=comparison_item.dxf_path,
        comparison_svg_path=comparison_item.svg_path,
        manufacturing_fingerprint=fingerprint,
        selected_hypothesis=selected_hypothesis,
    )
