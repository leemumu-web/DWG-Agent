from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite


BOX_EXPORT_PROFILE = "project_tekla_box_dxf_v1"
BOX_COMPILATION_REPORT_SCHEMA = "BOX-COMPILATION-REPORT-4.0"
BOX_AUTO_ACCEPTED_ROUTE = "auto_accepted"


@dataclass(frozen=True, slots=True)
class BoxSourceLimits:
    """Finite source budget absorbed from the retired BOX frontend."""

    max_entities: int = 200_000
    max_text_entities: int = 50_000
    max_points_per_entity: int = 20_000
    max_block_depth: int = 16
    max_abs_coordinate: float = 1.0e9

    def __post_init__(self) -> None:
        for name in (
            "max_entities",
            "max_text_entities",
            "max_points_per_entity",
            "max_block_depth",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not isfinite(self.max_abs_coordinate) or self.max_abs_coordinate <= 0:
            raise ValueError("max_abs_coordinate must be positive and finite")

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BoxSourceContract:
    """Caller-supplied provenance required before BOX source parsing."""

    source_system: str = "tekla_structures"
    drawing_kind: str = "single_part_drawing"
    member_family: str = "welded_box"
    export_profile: str = BOX_EXPORT_PROFILE

    def validate(self) -> None:
        expected = {
            "source_system": "tekla_structures",
            "drawing_kind": "single_part_drawing",
            "member_family": "welded_box",
            "export_profile": BOX_EXPORT_PROFILE,
        }
        violations = [
            f"{name}={getattr(self, name)!r}, expected {value!r}"
            for name, value in expected.items()
            if getattr(self, name) != value
        ]
        if violations:
            raise ValueError("BOX source contract violation: " + "; ".join(violations))

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
