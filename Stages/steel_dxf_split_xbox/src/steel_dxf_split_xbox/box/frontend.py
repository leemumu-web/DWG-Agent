from __future__ import annotations

from collections.abc import Iterator
from math import isfinite
from pathlib import Path

from .contracts import BoxSourceLimits
from .source_ir import SourceDocumentIR, SourceEntityIR, build_source_ir


class BoxSourceLimitError(ValueError):
    """A stable fail-closed result for a source resource budget overrun."""

    def __init__(
        self,
        reason_code: str,
        *,
        observed: int | float,
        limit: int | float,
    ) -> None:
        self.reason_code = reason_code
        self.observed = observed
        self.limit = limit
        super().__init__(f"{reason_code}: observed={observed}, limit={limit}")


def _raise_if_over(
    reason_code: str,
    observed: int | float,
    limit: int | float,
) -> None:
    if observed > limit:
        raise BoxSourceLimitError(
            reason_code,
            observed=observed,
            limit=limit,
        )


def _coordinate_values(entity: SourceEntityIR) -> Iterator[float]:
    for point in (entity.start, entity.end, entity.center, entity.major_axis):
        if point is not None:
            yield from point
    if entity.radius is not None:
        yield entity.radius
    for polyline_point in entity.points:
        yield from polyline_point


def _check_source_limits(source: SourceDocumentIR, limits: BoxSourceLimits) -> None:
    _raise_if_over(
        "source_entity_limit_exceeded",
        len(source.entities),
        limits.max_entities,
    )
    _raise_if_over(
        "source_text_limit_exceeded",
        sum(entity.text_raw is not None for entity in source.entities),
        limits.max_text_entities,
    )
    for entity in source.entities:
        _raise_if_over(
            "source_points_limit_exceeded",
            len(entity.points),
            limits.max_points_per_entity,
        )
        _raise_if_over(
            "source_block_depth_limit_exceeded",
            max(0, len(entity.source_id.split("/")) - 1),
            limits.max_block_depth,
        )
        for value in _coordinate_values(entity):
            if not isfinite(value) or abs(value) > limits.max_abs_coordinate:
                raise BoxSourceLimitError(
                    "source_coordinate_limit_exceeded",
                    observed=value,
                    limit=limits.max_abs_coordinate,
                )


def run_frontend(
    path: str | Path,
    *,
    limits: BoxSourceLimits = BoxSourceLimits(),
) -> SourceDocumentIR:
    """Build Project 2 SourceIR and enforce the compressed legacy budget."""

    source = build_source_ir(path)
    _check_source_limits(source, limits)
    return source
