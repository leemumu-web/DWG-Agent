from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


Point3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class PolylineSnapshot:
    handle: str
    layer: str
    color: int
    coordinates: tuple[float, ...]
    bulges: tuple[float, ...]
    closed: bool
    elevation: float
    normal: Point3


@dataclass(frozen=True, slots=True)
class CircleSnapshot:
    handle: str
    layer: str
    color: int
    center: Point3
    radius: float
    normal: Point3


@dataclass(frozen=True, slots=True)
class TextSnapshot:
    handle: str
    layer: str
    color: int
    text: str
    insertion_point: Point3
    height: float
    rotation: float
    text_alignment_point: Point3 | None = None
    alignment: int | None = None
    scale_factor: float | None = None


ReferenceEntitySnapshot = PolylineSnapshot | CircleSnapshot | TextSnapshot


@dataclass(frozen=True, slots=True)
class ReferenceSnapshot:
    path: Path
    sample_id: str
    source_relative_path: str
    source_sha256: str
    source_unchanged: bool
    zwcad_progid: str
    entities: tuple[ReferenceEntitySnapshot, ...]

    @property
    def foreign_member_labels(self) -> tuple[str, ...]:
        return tuple(
            entity.text
            for entity in self.entities
            if isinstance(entity, TextSnapshot) and self.sample_id not in entity.text
        )


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _point3(value: object, label: str) -> Point3:
    values = _sequence(value, label)
    if len(values) != 3:
        raise ValueError(f"{label} must contain three coordinates")
    return (
        _number(values[0], f"{label}[0]"),
        _number(values[1], f"{label}[1]"),
        _number(values[2], f"{label}[2]"),
    )


def _optional_point3(value: object, label: str) -> Point3 | None:
    return None if value is None else _point3(value, label)


def _common(entity: dict[str, Any], index: int) -> tuple[str, str, int]:
    prefix = f"entities[{index}]"
    return (
        _text(entity.get("handle"), f"{prefix}.handle"),
        _text(entity.get("layer"), f"{prefix}.layer"),
        _integer(entity.get("color"), f"{prefix}.color"),
    )


def _polyline(entity: dict[str, Any], index: int) -> PolylineSnapshot:
    prefix = f"entities[{index}]"
    handle, layer, color = _common(entity, index)
    coordinates = tuple(
        _number(value, f"{prefix}.coordinates")
        for value in _sequence(entity.get("coordinates"), f"{prefix}.coordinates")
    )
    if len(coordinates) < 6 or len(coordinates) % 2:
        raise ValueError(f"{prefix}.coordinates must contain complete 2D vertices")
    bulges = tuple(
        _number(value, f"{prefix}.bulges")
        for value in _sequence(entity.get("bulges"), f"{prefix}.bulges")
    )
    if len(bulges) != len(coordinates) // 2:
        raise ValueError(f"{prefix}.bulges must match the vertex count")
    closed = entity.get("closed")
    if not isinstance(closed, bool):
        raise ValueError(f"{prefix}.closed must be boolean")
    return PolylineSnapshot(
        handle=handle,
        layer=layer,
        color=color,
        coordinates=coordinates,
        bulges=bulges,
        closed=closed,
        elevation=_number(entity.get("elevation"), f"{prefix}.elevation"),
        normal=_point3(entity.get("normal"), f"{prefix}.normal"),
    )


def _circle(entity: dict[str, Any], index: int) -> CircleSnapshot:
    prefix = f"entities[{index}]"
    handle, layer, color = _common(entity, index)
    radius = _number(entity.get("radius"), f"{prefix}.radius")
    if radius <= 0:
        raise ValueError(f"{prefix}.radius must be positive")
    return CircleSnapshot(
        handle=handle,
        layer=layer,
        color=color,
        center=_point3(entity.get("center"), f"{prefix}.center"),
        radius=radius,
        normal=_point3(entity.get("normal"), f"{prefix}.normal"),
    )


def _text_entity(entity: dict[str, Any], index: int) -> TextSnapshot:
    prefix = f"entities[{index}]"
    handle, layer, color = _common(entity, index)
    alignment = entity.get("alignment")
    scale_factor = entity.get("scale_factor")
    return TextSnapshot(
        handle=handle,
        layer=layer,
        color=color,
        text=_text(entity.get("text"), f"{prefix}.text"),
        insertion_point=_point3(
            entity.get("insertion_point"), f"{prefix}.insertion_point"
        ),
        height=_number(entity.get("height"), f"{prefix}.height"),
        rotation=_number(entity.get("rotation"), f"{prefix}.rotation"),
        text_alignment_point=_optional_point3(
            entity.get("text_alignment_point"),
            f"{prefix}.text_alignment_point",
        ),
        alignment=None
        if alignment is None
        else _integer(alignment, f"{prefix}.alignment"),
        scale_factor=None
        if scale_factor is None
        else _number(scale_factor, f"{prefix}.scale_factor"),
    )


def load_entity_snapshot(
    path: str | Path,
    *,
    expected_source_sha256: str,
    expected_schema: str,
) -> ReferenceSnapshot:
    source = Path(path).resolve(strict=True)
    payload = _mapping(
        json.loads(source.read_text(encoding="utf-8-sig")),
        "snapshot",
    )
    if payload.get("schema") != expected_schema:
        raise ValueError("unsupported reference snapshot schema")
    source_proof = _mapping(payload.get("source"), "source")
    expected = expected_source_sha256.casefold()
    before = _text(source_proof.get("sha256_before"), "source.sha256_before").casefold()
    after = _text(source_proof.get("sha256_after"), "source.sha256_after").casefold()
    unchanged = source_proof.get("unchanged")
    if before != expected or after != expected or unchanged is not True:
        raise ValueError("reference source hash proof is invalid")

    raw_entities = _sequence(payload.get("entities"), "entities")
    if payload.get("model_space_count") != len(raw_entities):
        raise ValueError("reference entity count does not match model space")
    entities: list[ReferenceEntitySnapshot] = []
    for index, raw_entity in enumerate(raw_entities):
        entity = _mapping(raw_entity, f"entities[{index}]")
        object_name = entity.get("object_name")
        if object_name == "AcDbPolyline":
            entities.append(_polyline(entity, index))
        elif object_name == "AcDbCircle":
            entities.append(_circle(entity, index))
        elif object_name == "AcDbText":
            entities.append(_text_entity(entity, index))
        else:
            raise ValueError(f"unsupported reference entity: {object_name!r}")

    return ReferenceSnapshot(
        path=source,
        sample_id=_text(payload.get("sample_id"), "sample_id"),
        source_relative_path=_text(
            source_proof.get("relative_path"), "source.relative_path"
        ),
        source_sha256=before,
        source_unchanged=True,
        zwcad_progid=_text(payload.get("zwcad_progid"), "zwcad_progid"),
        entities=tuple(entities),
    )


def load_reference_snapshot(
    path: str | Path,
    *,
    expected_source_sha256: str,
) -> ReferenceSnapshot:
    return load_entity_snapshot(
        path,
        expected_source_sha256=expected_source_sha256,
        expected_schema="BOX-YELLOW-REFERENCE-SNAPSHOT-1.0",
    )
