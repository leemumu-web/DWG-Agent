from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .bh_associations import DrawingGraph, DrawingNodeKind
from .bh_dialect import canonical_tekla_layer, canonical_tekla_linetype
from .bh_geometry import PartBlock, entities_bbox
from .bh_ir import BHDocumentIR, SemanticLayer, TextAtom
from .bh_trace import DecisionRecord, Evidence
from .bh_models import BHMetadata, HProfile

_H_RE = re.compile(
    r"(?<![A-Z0-9])(?P<family>BH)\s*"
    r"(?P<h1>\d+(?:\.\d+)?)"
    r"(?:\s*[-~～—]\s*(?P<h2>\d+(?:\.\d+)?))?\s*[xX×*]\s*"
    r"(?P<b>\d+(?:\.\d+)?)\s*[xX×*]\s*"
    r"(?P<tw>\d+(?:\.\d+)?)\s*[xX×*]\s*"
    r"(?P<tf>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_SCALE_RE = re.compile(r"(?<!\d)1\s*:\s*(?P<scale>\d+(?:\.\d+)?)")
_PART_RE = re.compile(r"(?i)^[a-z0-9]+(?:-[a-z0-9]+)+$")
_MATERIAL_RE = re.compile(r"(?i)^Q\d{3}[A-Z0-9-]*$")
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")


@dataclass(slots=True)
class MetadataParseResult:
    metadata: BHMetadata
    decision: DecisionRecord
    table_block_handle: str
    row_tokens: list[TextAtom]
    fallback_fields: tuple[str, ...] = ()


@dataclass(slots=True)
class ViewSelectionResult:
    main: PartBlock
    flange: PartBlock
    decision: DecisionRecord
    candidates: list[dict[str, Any]]


def _parse_profile(token: TextAtom) -> HProfile:
    match = _H_RE.search(token.normalized)
    if match is None:
        raise ValueError(f"Not a BH profile token: {token.normalized}")
    raw = match.group(0).upper().replace("X", "*").replace("×", "*")
    return HProfile(
        height=float(match.group("h1")),
        secondary_height=float(match.group("h2")) if match.group("h2") else None,
        flange_width=float(match.group("b")),
        web_thickness=float(match.group("tw")),
        flange_thickness=float(match.group("tf")),
        raw_text=raw,
    )


def _same_row(tokens: list[TextAtom], anchor: TextAtom) -> list[TextAtom]:
    tolerance = max(2.0, anchor.height * 0.35)
    row = [item for item in tokens if abs(item.position.y - anchor.position.y) <= tolerance]
    return sorted(row, key=lambda item: item.position.x)


def _nearest_left(tokens: list[TextAtom], anchor: TextAtom, pattern: re.Pattern[str]) -> TextAtom | None:
    candidates = [
        item
        for item in tokens
        if item.position.x < anchor.position.x and pattern.fullmatch(item.normalized)
    ]
    return max(candidates, key=lambda item: item.position.x, default=None)


def _nearest_physical_length(
    tokens: list[TextAtom],
    anchor: TextAtom,
    _profile: HProfile,
) -> TextAtom | None:
    """Select a row-local member length without assuming left-to-right layout."""

    candidates = [
        item
        for item in tokens
        if item is not anchor
        and _NUMBER_RE.fullmatch(item.normalized)
    ]
    return min(
        candidates,
        key=lambda item: (
            abs(item.position.x - anchor.position.x),
            abs(item.position.y - anchor.position.y),
            item.normalized,
        ),
        default=None,
    )


def parse_bh_metadata_ir(
    ir: BHDocumentIR,
    source_path: Path | None = None,
    *,
    drawing_graph: DrawingGraph | None = None,
) -> MetadataParseResult:
    candidates: list[tuple[float, Any, TextAtom, list[TextAtom], dict[str, Any]]] = []
    graph_rows = (
        drawing_graph.nodes_of(DrawingNodeKind.METADATA_ROW)
        if drawing_graph is not None
        else []
    )
    atoms_by_source: dict[str, list[tuple[Any, TextAtom]]] = {}
    for block in ir.blocks:
        for text in block.texts:
            atoms_by_source.setdefault(text.source.source_id, []).append((block, text))
    for row_node in graph_rows:
        pairs = [
            pair
            for source_id in row_node.source_ids
            for pair in atoms_by_source.get(source_id, [])
        ]
        if not pairs:
            continue
        axis = row_node.attributes.get("axis", [1.0, 0.0])
        row = sorted(
            (pair[1] for pair in pairs),
            key=lambda token: (
                token.position.x * float(axis[0]) + token.position.y * float(axis[1]),
                token.normalized,
            ),
        )
        profile_token = next((item for item in row if _H_RE.search(item.normalized)), None)
        if profile_token is None:
            continue
        block = next(pair[0] for pair in pairs if pair[1] is profile_token)
        part = next(
            (
                item
                for item in row
                if _PART_RE.fullmatch(item.normalized)
                and not _MATERIAL_RE.fullmatch(item.normalized)
            ),
            None,
        )
        length_value = float(row_node.attributes["nominal_length_mm"])
        length = min(
            (
                item
                for item in row
                if _NUMBER_RE.fullmatch(item.normalized)
            ),
            key=lambda item: abs(float(item.normalized) - length_value),
            default=None,
        )
        material = next(
            (item for item in row if _MATERIAL_RE.fullmatch(item.normalized)),
            None,
        )
        scale = next((item for item in row if _SCALE_RE.search(item.normalized)), None)
        fields = {"part": part, "length": length, "material": material, "scale": scale}
        completeness = sum(item is not None for item in fields.values())
        candidates.append(
            (
                100.0 + 10.0 * completeness + min(len(row), 8),
                block,
                profile_token,
                row,
                fields,
            )
        )

    for block in (() if candidates else ir.blocks):
        profile_tokens = [item for item in block.texts if _H_RE.search(item.normalized)]
        for profile_token in profile_tokens:
            row = _same_row(block.texts, profile_token)
            parsed_profile = _parse_profile(profile_token)
            expected_part = (
                source_path.stem.replace("_拆板前", "").casefold()
                if source_path is not None
                else None
            )
            part = next(
                (
                    item
                    for item in row
                    if expected_part is not None
                    and item.normalized.casefold() == expected_part
                ),
                None,
            )
            part = part or _nearest_left(row, profile_token, _PART_RE)
            if part is None:
                part = min(
                    (
                        item
                        for item in row
                        if _PART_RE.fullmatch(item.normalized)
                        and not _MATERIAL_RE.fullmatch(item.normalized)
                    ),
                    key=lambda item: abs(item.position.x - profile_token.position.x),
                    default=None,
                )
            length = _nearest_physical_length(row, profile_token, parsed_profile)
            material = next((item for item in row if _MATERIAL_RE.fullmatch(item.normalized)), None)
            scale = next((item for item in row if _SCALE_RE.search(item.normalized)), None)
            completeness = sum(item is not None for item in (part, length, material, scale))
            # A coherent row is much stronger evidence than a block-wide text bag.
            row_spread = max((abs(item.position.y - profile_token.position.y) for item in row), default=0.0)
            score = 10.0 * completeness + min(len(row), 8) - row_spread / max(profile_token.height, 1.0)
            candidates.append(
                (
                    score,
                    block,
                    profile_token,
                    row,
                    {"part": part, "length": length, "material": material, "scale": scale},
                )
            )
    if not candidates:
        raise ValueError("Could not locate a material-table row containing a BH profile.")
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, block, profile_token, row, fields = candidates[0]
    profile = _parse_profile(profile_token)
    fallback_fields: list[str] = []

    part_token: TextAtom | None = fields["part"]
    if part_token is None:
        fallback_fields.append("part_number")
        all_parts = [item for item in block.texts if _PART_RE.fullmatch(item.normalized)]
        if source_path is not None:
            stem = source_path.stem.replace("_拆板前", "").lower()
            part_token = next((item for item in all_parts if item.normalized.lower() == stem), None)
        part_token = part_token or min(all_parts, key=lambda item: len(item.normalized), default=None)
    if part_token is None:
        if source_path is None:
            raise ValueError("Part number was not found in the material-table row.")
        part_number = source_path.stem.replace("_拆板前", "")
    else:
        part_number = part_token.normalized

    length_token: TextAtom | None = fields["length"]
    if length_token is None:
        fallback_fields.append("nominal_length")
        plausible_numbers = [
            item
            for item in block.texts
            if _NUMBER_RE.fullmatch(item.normalized)
        ]
        length_token = min(
            plausible_numbers,
            key=lambda item: (
                abs(item.position.y - profile_token.position.y),
                abs(item.position.x - profile_token.position.x),
            ),
            default=None,
        )
    if length_token is None:
        raise ValueError("Nominal length was not found adjacent to the profile row.")

    material_token: TextAtom | None = fields["material"]
    if material_token is None:
        fallback_fields.append("material")
    scale_token: TextAtom | None = fields["scale"]
    if scale_token is None:
        scale_token = next((item for item in block.texts if _SCALE_RE.search(item.normalized)), None)
    scale_match = _SCALE_RE.search(scale_token.normalized) if scale_token else None
    drawing_scale = float(scale_match.group("scale")) if scale_match else 1.0

    metadata = BHMetadata(
        part_number=part_number,
        profile=profile,
        nominal_length=float(length_token.normalized),
        material=material_token.normalized.upper() if material_token else None,
        drawing_scale=drawing_scale,
        material_table_handle=block.handle,
    )
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0
    margin = max(0.0, score - second_score)
    completeness = sum(fields[key] is not None for key in ("part", "length", "material", "scale"))
    confidence = min(1.0, 0.40 + 0.12 * completeness + min(margin / 20.0, 0.12))
    evidence = [
        Evidence(
            "metadata.profile",
            "BH profile is parsed from one material-table row.",
            1.0,
            profile.raw_text,
            (profile_token.source.stable_id,),
        ),
        Evidence(
            "metadata.row_alignment",
            "Part number, profile, length, material and scale are aligned spatially on the same row.",
            0.9,
            [item.normalized for item in row],
            tuple(item.source.stable_id for item in row),
        ),
        Evidence(
            "metadata.source_filename",
            "The source filename is used only as a fallback or exact-match confirmation, not as the primary parser.",
            0.2,
            source_path.stem if source_path else None,
        ),
    ]
    alternatives = [
        {
            "block": item[1].name,
            "handle": item[1].handle,
            "score": item[0],
            "profile": item[2].normalized,
            "row": [token.normalized for token in item[3]],
        }
        for item in candidates[:5]
    ]
    decision = DecisionRecord(
        name="material_table_row",
        selected=f"{block.name}:{block.handle}",
        score=score,
        confidence=confidence,
        margin=margin,
        alternatives=alternatives,
        evidence=evidence,
        warnings=[] if completeness == 4 else ["One or more metadata fields required a block-level fallback."],
    )
    return MetadataParseResult(
        metadata,
        decision,
        block.handle,
        row,
        tuple(sorted(set(fallback_fields))),
    )


def part_blocks_from_ir(ir: BHDocumentIR) -> list[PartBlock]:
    blocks: list[PartBlock] = []
    for block in ir.blocks:
        entities = []
        entity_source_ids: list[str] = []
        for atom in block.entities:
            if (
                atom.semantic_layer != SemanticLayer.PART_EDGE
                or atom.entity.dxftype() not in {"LINE", "ARC"}
            ):
                continue
            entity = atom.entity.copy()
            entity.dxf.layer = canonical_tekla_layer(atom.semantic_layer) or entity.dxf.layer
            entity.dxf.linetype = canonical_tekla_linetype(
                atom.visibility,
                str(entity.dxf.linetype),
            )
            entities.append(entity)
            entity_source_ids.append(atom.source.stable_id)
        if len(entities) < 4:
            continue
        blocks.append(
            PartBlock(
                block.insert,
                entities,
                entities_bbox(entities),
                source_view=block.source_view,
                entity_source_ids=tuple(entity_source_ids),
            )
        )
    return blocks


def _dimension_residual(block: PartBlock, nominal_length: float, transverse: float) -> tuple[float, str]:
    direct = abs(block.bbox.width - nominal_length) / max(nominal_length, 1.0) + abs(block.bbox.height - transverse) / max(transverse, 1.0)
    rotated = abs(block.bbox.height - nominal_length) / max(nominal_length, 1.0) + abs(block.bbox.width - transverse) / max(transverse, 1.0)
    return (direct, "x") if direct <= rotated else (rotated, "y")


def _view_features(block: PartBlock) -> dict[str, float]:
    arc_count = sum(entity.dxftype() == "ARC" for entity in block.entities)
    hidden_count = sum(str(entity.dxf.linetype).upper() == "XKITLINE04" for entity in block.entities)
    return {
        "entity_count": float(len(block.entities)),
        "arc_count": float(arc_count),
        "hidden_ratio": hidden_count / max(len(block.entities), 1),
        "area": block.bbox.width * block.bbox.height,
    }


def select_bh_views_ir(ir: BHDocumentIR, metadata: BHMetadata) -> ViewSelectionResult:
    blocks = part_blocks_from_ir(ir)
    if len(blocks) < 2:
        raise ValueError(f"Expected at least two Part blocks, found {len(blocks)}.")
    pairs: list[dict[str, Any]] = []
    for main in blocks:
        main_residual, main_axis = _dimension_residual(
            main, metadata.nominal_length, metadata.profile.max_height
        )
        main_features = _view_features(main)
        for flange in blocks:
            if flange.handle == main.handle:
                continue
            flange_residual, flange_axis = _dimension_residual(
                flange, metadata.nominal_length, metadata.profile.flange_width
            )
            flange_features = _view_features(flange)
            axis_penalty = 0.0 if main_axis == flange_axis else 0.03
            # Main views typically have more transverse complexity/arcs, while
            # flange projections are usually simpler. This is a weak tie-breaker,
            # never a hard rule.
            complexity_penalty = max(0.0, flange_features["arc_count"] - main_features["arc_count"]) * 0.002
            score = main_residual + flange_residual + axis_penalty + complexity_penalty
            pairs.append(
                {
                    "main": main,
                    "flange": flange,
                    "score": score,
                    "main_residual": main_residual,
                    "flange_residual": flange_residual,
                    "main_axis": main_axis,
                    "flange_axis": flange_axis,
                    "main_features": main_features,
                    "flange_features": flange_features,
                }
            )
    pairs.sort(key=lambda item: item["score"])
    selected = pairs[0]
    if selected["main_residual"] > 0.40:
        raise ValueError("No Part block matches the BH longitudinal web view.")
    if selected["flange_residual"] > 0.40:
        raise ValueError("No Part block matches the BH flange projection view.")
    second_score = pairs[1]["score"] if len(pairs) > 1 else selected["score"] + 1.0
    margin = max(0.0, second_score - selected["score"])
    confidence = max(0.0, min(1.0, 1.0 - selected["score"] / 0.80 + min(margin / 0.20, 0.20)))
    alternatives = [
        {
            "main_block": item["main"].name,
            "main_handle": item["main"].handle,
            "flange_block": item["flange"].name,
            "flange_handle": item["flange"].handle,
            "score": item["score"],
            "main_residual": item["main_residual"],
            "flange_residual": item["flange_residual"],
            "main_axis": item["main_axis"],
            "flange_axis": item["flange_axis"],
        }
        for item in pairs[:8]
    ]
    evidence = [
        Evidence(
            "view.web_dimensions",
            "Web view dimensions are compared with nominal member length and maximum profile height in both orientations.",
            1.0,
            {"bbox": [selected["main"].bbox.width, selected["main"].bbox.height], "residual": selected["main_residual"]},
        ),
        Evidence(
            "view.flange_dimensions",
            "Flange projection dimensions are compared with nominal member length and flange width in both orientations.",
            1.0,
            {"bbox": [selected["flange"].bbox.width, selected["flange"].bbox.height], "residual": selected["flange_residual"]},
        ),
        Evidence(
            "view.global_pairing",
            "Main and flange views are selected as one globally scored ordered pair instead of two independent greedy choices.",
            0.8,
            {"pair_score": selected["score"], "margin": margin},
        ),
    ]
    decision = DecisionRecord(
        name="view_pair",
        selected=f"main={selected['main'].name}:{selected['main'].handle}; flange={selected['flange'].name}:{selected['flange'].handle}",
        score=float(selected["score"]),
        confidence=confidence,
        margin=margin,
        alternatives=alternatives,
        evidence=evidence,
        warnings=[] if margin >= 0.01 else ["View-pair score margin is small; inspect alternative candidates in the report."],
    )
    return ViewSelectionResult(
        main=selected["main"],
        flange=selected["flange"],
        decision=decision,
        candidates=alternatives,
    )
