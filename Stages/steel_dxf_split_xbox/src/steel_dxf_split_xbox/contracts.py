from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

XBOX_EXPORT_PROFILE = "project_tekla_xbox_dxf_v1"


class XboxSplitError(RuntimeError):
    """Fatal, contract-level XBOX split failure with a stable error code."""

    def __init__(self, code: str, message_zh: str) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


@dataclass(frozen=True, slots=True)
class XboxSourceContract:
    """Source contract that must pin every XBOX drawing handed to this Stage."""

    source_system: str = "tekla_structures"
    drawing_kind: str = "single_part_drawing"
    member_family: str = "welded_xbox"
    export_profile: str = XBOX_EXPORT_PROFILE

    def validate(self) -> None:
        expected = {
            "source_system": "tekla_structures",
            "drawing_kind": "single_part_drawing",
            "member_family": "welded_xbox",
            "export_profile": XBOX_EXPORT_PROFILE,
        }
        violations = [
            f"{name}={getattr(self, name)!r}, expected {value!r}"
            for name, value in expected.items()
            if getattr(self, name) != value
        ]
        if violations:
            raise ValueError("XBOX source contract violation: " + "; ".join(violations))

    def to_dict(self) -> dict[str, str]:
        return {
            "source_system": self.source_system,
            "drawing_kind": self.drawing_kind,
            "member_family": self.member_family,
            "export_profile": self.export_profile,
        }


def member_name(input_path: Path) -> str:
    """Stable per-drawing task name derived from the frozen input file name."""

    name = (
        input_path.stem.replace("_拆板前", "")
        .replace("拆板前", "")
        .rstrip("_- ")
    )
    if not name:
        raise ValueError("input drawing has an empty member name")
    return name


__all__ = [
    "XBOX_EXPORT_PROFILE",
    "XboxSourceContract",
    "XboxSplitError",
    "member_name",
]
