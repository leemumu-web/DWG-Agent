from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Iterable

from .bh_canonical import canonical_sha256, canonical_source_payload
from .bh_hypothesis import AssemblyHypothesis
from .bh_ir import BHDocumentIR
from .bh_manufacturing_ir import BHManufacturingIR
from .bh_source import SourceDocument
from .bh_models import BHAssembly, BulgeContour


# 指纹规范化精度：6 位小数 ≈ 0.001mm。低于 DXF 双精度（吸收子微米拓扑
# 噪声，保证跨版本/跨平台哈希稳定），又高于制造公差（不吞真实几何差异）。
# 指纹契约承诺：「相同制造解释必等值、不同解释不等值」；改动会破坏
# 下游一致性校验的历史指纹，必须走版本升级。
FLOAT_DIGITS = 6


def _number(value: float) -> float:
    rounded = round(float(value), FLOAT_DIGITS)
    return 0.0 if rounded == -0.0 else rounded


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _canonical(asdict(value))
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _sha256(payload: Any) -> str:
    encoded = json.dumps(
        _canonical(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rotations(items: list[tuple[float, float, float]]) -> Iterable[tuple[tuple[float, float, float], ...]]:
    for index in range(len(items)):
        yield tuple(items[index:] + items[:index])


def canonical_contour(contour: BulgeContour) -> list[list[float]]:
    """Canonicalize a cyclic bulge contour independent of start vertex and winding."""

    bbox = contour.bbox
    forward = [
        (
            _number(vertex.x - bbox.min_x),
            _number(vertex.y - bbox.min_y),
            _number(vertex.bulge),
        )
        for vertex in contour.vertices
    ]
    # For a reversed contour, the bulge at a vertex belongs to the edge toward
    # the next reversed vertex, i.e. the negative bulge of the previous forward
    # edge.  Canonicalizing both windings prevents arbitrary DXF start/winding
    # choices from changing the manufacturing identity.
    reversed_items: list[tuple[float, float, float]] = []
    count = len(forward)
    for new_index in range(count):
        old_index = (-new_index) % count
        previous_edge = (old_index - 1) % count
        x, y, _ = forward[old_index]
        reversed_items.append((x, y, _number(-forward[previous_edge][2])))
    best = min((*_rotations(forward), *_rotations(reversed_items)))
    return [[x, y, bulge] for x, y, bulge in best]


def document_fact_payload(ir: BHDocumentIR) -> dict[str, Any]:
    return {
        "dxf_version": ir.dxf_version,
        "encoding": ir.encoding,
        "units": ir.units,
        "audit_error_count": ir.audit_error_count,
        "direct_entity_counts": ir.direct_entity_counts,
        "blocks": [
            {
                "handle": block.handle,
                "name": block.name,
                "transform": asdict(block.transform),
                "bbox": asdict(block.bbox) if block.bbox else None,
                "entities": [
                    {
                        "stable_id": atom.source.stable_id,
                        "type": atom.source.entity_type,
                        "layer": atom.source.layer,
                        "linetype": atom.source.linetype,
                        "semantic_layer": atom.semantic_layer.value,
                        "visibility": atom.visibility.value,
                        "bbox": asdict(atom.bbox) if atom.bbox else None,
                    }
                    for atom in sorted(block.entities, key=lambda item: item.source.stable_id)
                ],
                "texts": [
                    {
                        "normalized": text.normalized,
                        "position": [_number(text.position.x), _number(text.position.y)],
                        "height": _number(text.height),
                        "rotation": _number(text.rotation),
                        "source": text.source.stable_id,
                    }
                    for text in sorted(block.texts, key=lambda item: item.source.stable_id)
                ],
            }
            for block in sorted(ir.blocks, key=lambda item: (item.handle, item.name))
        ],
    }


def manufacturing_payload(assembly: BHAssembly) -> dict[str, Any]:
    plates = []
    for plate in assembly.plates:
        plates.append(
            {
                "role": plate.role.value,
                "label": plate.label,
                "quantity": plate.quantity,
                "thickness_mm": _number(plate.thickness),
                "outer_contour": canonical_contour(plate.contour),
                "circular_cuts": sorted(
                    [
                        [
                            _number(cut.center.x - plate.bbox.min_x),
                            _number(cut.center.y - plate.bbox.min_y),
                            _number(cut.radius),
                        ]
                        for cut in plate.circular_cuts
                    ]
                ),
                "inner_contours": sorted(
                    [canonical_contour(contour) for contour in plate.inner_contours],
                    key=lambda item: json.dumps(item, separators=(",", ":")),
                ),
            }
        )
    plates.sort(
        key=lambda item: (
            0 if item["role"] == "web" else 1,
            item["label"],
            json.dumps(item["outer_contour"], separators=(",", ":")),
        )
    )
    profile = assembly.metadata.profile
    return {
        "part_number": assembly.metadata.part_number,
        "profile": {
            "height": _number(profile.height),
            "secondary_height": _number(profile.secondary_height) if profile.secondary_height is not None else None,
            "flange_width": _number(profile.flange_width),
            "web_thickness": _number(profile.web_thickness),
            "flange_thickness": _number(profile.flange_thickness),
        },
        "nominal_length_mm": _number(assembly.metadata.nominal_length),
        "material": assembly.metadata.material,
        "plates": plates,
    }


def manufacturing_fingerprint(assembly: BHAssembly) -> str:
    """Return the canonical identity of one manufacturing interpretation."""

    return _sha256(manufacturing_payload(assembly))


def hypothesis_payload(hypothesis: AssemblyHypothesis) -> dict[str, Any]:
    return {
        "view_pair": {
            "main_handle": hypothesis.view_pair.main.handle,
            "flange_handle": hypothesis.view_pair.flange.handle,
            "main_axis": hypothesis.view_pair.main_axis,
            "flange_axis": hypothesis.view_pair.flange_axis,
        },
        "hard_pass": hypothesis.hard_pass,
        "rules": [
            {
                "rule_id": rule.rule_id,
                "hard": rule.hard,
                "satisfied": rule.satisfied,
                "quality": _number(rule.quality),
            }
            for rule in sorted(hypothesis.rules, key=lambda item: item.rule_id)
        ],
        "manufacturing": manufacturing_payload(hypothesis.assembly) if hypothesis.assembly else None,
    }


def build_semantic_fingerprints(
    source_ir: SourceDocument,
    ir: BHDocumentIR,
    selected: AssemblyHypothesis,
    manufacturing_ir: BHManufacturingIR | None = None,
) -> dict[str, str]:
    if selected.assembly is None:
        raise ValueError("Cannot fingerprint a hypothesis without manufacturing IR.")
    fact = canonical_source_payload(source_ir, grid_mm=1e-6)
    hypothesis = hypothesis_payload(selected)
    return {
        "algorithm": "sha256-canonical-json-v3",
        "source_fact_ir": canonical_sha256(fact),
        "selected_hypothesis": _sha256(hypothesis),
        "manufacturing_ir": (
            manufacturing_ir.fingerprint
            if manufacturing_ir is not None
            else manufacturing_fingerprint(selected.assembly)
        ),
        "writer_assembly": manufacturing_fingerprint(selected.assembly),
    }
