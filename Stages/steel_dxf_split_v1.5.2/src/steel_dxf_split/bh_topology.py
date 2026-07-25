from __future__ import annotations

from dataclasses import dataclass

from .bh_source import SourceEntity


@dataclass(frozen=True, slots=True)
class SourceComponent:
    component_id: str
    entities: tuple[SourceEntity, ...]


def connected_source_components(
    entities: tuple[SourceEntity, ...],
    *,
    snap: float = 0.01,
) -> tuple[SourceComponent, ...]:
    """Group source curves that share a quantized vertex or endpoint."""

    if snap <= 0.0:
        raise ValueError("Topology snap must be positive.")
    if not entities:
        return ()
    parent = list(range(len(entities)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    vertices: dict[tuple[int, int], list[int]] = {}
    for index, entity in enumerate(entities):
        geometry = entity.geometry
        if geometry is None:
            continue
        for x, y in geometry.coordinates:
            key = (int(round(x / snap)), int(round(y / snap)))
            vertices.setdefault(key, []).append(index)
    for indices in vertices.values():
        for index in indices[1:]:
            union(indices[0], index)

    grouped: dict[int, list[SourceEntity]] = {}
    for index, entity in enumerate(entities):
        grouped.setdefault(find(index), []).append(entity)
    components = []
    for items in grouped.values():
        ordered = tuple(sorted(items, key=lambda item: item.source_id))
        components.append(
            SourceComponent(
                component_id="component:" + ordered[0].source_id[:16],
                entities=ordered,
            )
        )
    return tuple(
        sorted(
            components,
            key=lambda item: (
                -len(item.entities),
                item.component_id,
            ),
        )
    )
