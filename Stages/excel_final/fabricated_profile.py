"""Single parser and geometry value object for BH, BOX, and BT profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from multi_split.profile import FabricatedChildGeometry, split_fabricated_geometry


class FabricatedProfileError(ValueError):
    """Raised when a fabricated-profile prefix has invalid geometry."""


_NUMBER = r"\d+(?:\.\d+)?"
_FABRICATED = re.compile(
    rf"^(?P<kind>BH|BOX|BT)(?P<height>{_NUMBER})\*(?P<width>{_NUMBER})"
    rf"\*(?P<web>{_NUMBER})(?:\*(?P<flange>{_NUMBER}))?$"
)


def _compact(spec: object) -> str:
    compact = str(spec or "").replace(" ", "").replace("　", "").upper()
    return re.sub(r"(?<=\d)[X×](?=\d)", "*", compact)


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise FabricatedProfileError(
            f"invalid fabricated-profile dimension: {value!r}"
        ) from exc
    if not result.is_finite():
        raise FabricatedProfileError("fabricated-profile dimensions must be finite")
    return result


def _number_text(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


@dataclass(frozen=True, slots=True)
class FabricatedProfile:
    kind: str
    height: Decimal
    width: Decimal
    web_thickness: Decimal
    flange_thickness: Decimal

    def __post_init__(self) -> None:
        if self.kind not in {"BH", "BOX", "BT"}:
            raise FabricatedProfileError(
                f"unsupported fabricated profile: {self.kind}"
            )
        try:
            split_fabricated_geometry(
                self.kind,
                self.height,
                self.width,
                self.web_thickness,
                self.flange_thickness,
            )
        except ValueError as exc:
            raise FabricatedProfileError(str(exc)) from exc

    @property
    def normalized_spec(self) -> str:
        dimensions = (
            self.height,
            self.width,
            self.web_thickness,
            self.flange_thickness,
        )
        return self.kind + "*".join(_number_text(value) for value in dimensions)

    def children(
        self,
    ) -> tuple[FabricatedChildGeometry, FabricatedChildGeometry]:
        return split_fabricated_geometry(
            self.kind,
            self.height,
            self.width,
            self.web_thickness,
            self.flange_thickness,
        )

    @property
    def cross_section_area(self) -> Decimal:
        return sum(
            child.thickness * child.width * child.quantity_multiplier
            for child in self.children()
        )


def parse_fabricated_profile(spec: object) -> FabricatedProfile | None:
    """Parse one fabricated profile, returning None for unrelated specifications."""
    compact = _compact(spec)
    if not compact.startswith(("BH", "BOX", "BT")):
        return None
    match = _FABRICATED.fullmatch(compact)
    if match is None:
        raise FabricatedProfileError(
            f"fabricated-profile specification is invalid: {compact!r}"
        )
    kind = match.group("kind")
    height = _decimal(match.group("height"))
    width = _decimal(match.group("width"))
    web = _decimal(match.group("web"))
    flange_text = match.group("flange")
    if flange_text is None:
        if kind != "BOX":
            raise FabricatedProfileError(
                f"{kind} requires four dimensions"
            )
        flange = web
    else:
        flange = _decimal(flange_text)
    return FabricatedProfile(kind, height, width, web, flange)
