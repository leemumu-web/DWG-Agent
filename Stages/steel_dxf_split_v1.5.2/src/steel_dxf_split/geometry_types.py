from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Point2D:
    """A unit-agnostic two-dimensional point used by compiler IRs."""

    x: float
    y: float

    def translated(self, dx: float, dy: float) -> "Point2D":
        return Point2D(self.x + dx, self.y + dy)

    def scaled(self, factor: float) -> "Point2D":
        return Point2D(self.x * factor, self.y * factor)

    def distance_to(self, other: "Point2D") -> float:
        return hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Axis-aligned bounds in the coordinate system of the owning IR."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def center(self) -> Point2D:
        return Point2D(
            (self.min_x + self.max_x) / 2.0,
            (self.min_y + self.max_y) / 2.0,
        )

    def expanded(self, amount: float) -> "BoundingBox":
        return BoundingBox(
            self.min_x - amount,
            self.min_y - amount,
            self.max_x + amount,
            self.max_y + amount,
        )

    @classmethod
    def from_points(cls, points: Iterable[Point2D]) -> "BoundingBox":
        values = tuple(points)
        if not values:
            raise ValueError("Cannot build a bounding box from no points.")
        return cls(
            min(point.x for point in values),
            min(point.y for point in values),
            max(point.x for point in values),
            max(point.y for point in values),
        )
