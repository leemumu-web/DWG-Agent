"""Read-only parser for frozen historical BOX split results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .manual_reference import (
    ManualOpening,
    ManualPlate,
    _assign_reference,
    _label,
    _polyline_shape,
)
from .reference_snapshot import (
    CircleSnapshot,
    PolylineSnapshot,
    TextSnapshot,
    load_entity_snapshot,
)


@dataclass(frozen=True, slots=True)
class HistoricalPlateSet:
    path: Path
    sample_id: str
    source_relative_path: str
    source_sha256: str
    member_mark: str
    plates: tuple[ManualPlate, ...]
    evidence_warnings: tuple[str, ...] = ()


def load_historical_result(
    path: str | Path,
    *,
    expected_source_sha256: str,
    expected_member_mark: str | None = None,
) -> HistoricalPlateSet:
    snapshot = load_entity_snapshot(
        path,
        expected_source_sha256=expected_source_sha256,
        expected_schema="BOX-HISTORICAL-WRONG-RESULT-SNAPSHOT-1.0",
    )
    shapes = tuple(
        _polyline_shape(entity)
        for entity in snapshot.entities
        if isinstance(entity, PolylineSnapshot)
    )
    opening_handles = {
        entity.handle
        for entity in snapshot.entities
        if isinstance(entity, PolylineSnapshot) and entity.layer == "CUT_HOLE"
    }
    openings = tuple(
        ManualOpening.polygon(
            entity_handle=entity.handle,
            shape=next(shape for shape in shapes if shape.entity_handle == entity.handle),
            source_bulges=entity.bulges,
        )
        for entity in snapshot.entities
        if isinstance(entity, PolylineSnapshot) and entity.handle in opening_handles
    ) + tuple(
        ManualOpening.circle(
            entity_handle=entity.handle,
            center=entity.center,
            radius=entity.radius,
        )
        for entity in snapshot.entities
        if isinstance(entity, CircleSnapshot)
    )
    labels = tuple(
        parsed
        for entity in snapshot.entities
        if isinstance(entity, TextSnapshot)
        if (
            parsed := _label(
                entity.text,
                (entity.insertion_point[0], entity.insertion_point[1]),
            )
        )
        is not None
    )
    reference = _assign_reference(
        path=snapshot.path,
        expected_member_mark=expected_member_mark or snapshot.sample_id,
        labels=labels,
        plate_shapes=tuple(
            shape for shape in shapes if shape.entity_handle not in opening_handles
        ),
        openings=openings,
    )
    return HistoricalPlateSet(
        path=snapshot.path,
        sample_id=snapshot.sample_id,
        source_relative_path=snapshot.source_relative_path,
        source_sha256=snapshot.source_sha256,
        member_mark=reference.member_mark,
        plates=reference.plates,
        evidence_warnings=reference.evidence_warnings,
    )
