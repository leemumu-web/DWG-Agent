"""Single parser and geometry value object for BH, BOX, and BT profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

class FabricatedProfileError(ValueError):
    """Raised when a fabricated-profile prefix has invalid geometry."""


_NUMBER = r"\d+(?:\.\d+)?"
# BBH is a variable-height BH whose web tapers between two end heights,
# written H1-H2 (also ~ / ～ / —).  BH/BOX/BT stay constant-section.
_FABRICATED = re.compile(
    rf"^(?P<kind>BBH|BH|BOX|BT)(?P<height>{_NUMBER})"
    rf"(?:\s*[-~～—]\s*(?P<secondary>{_NUMBER}))?"
    rf"\*(?P<width>{_NUMBER})"
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
class FabricatedChildGeometry:
    part_type: str
    thickness: Decimal
    width: Decimal
    quantity_multiplier: Decimal
    is_main: bool


def _split_geometry(
    kind: str,
    height: Decimal,
    width: Decimal,
    web_thickness: Decimal,
    flange_thickness: Decimal,
    secondary_height: Decimal | None = None,
) -> tuple[FabricatedChildGeometry, FabricatedChildGeometry]:
    if kind == "BT":
        web_width = height - flange_thickness
        web_multiplier = flange_multiplier = Decimal("1")
    elif kind == "BOX":
        web_width = height - Decimal("2") * flange_thickness
        web_multiplier = flange_multiplier = Decimal("2")
    elif kind == "BH":
        web_width = height - Decimal("2") * flange_thickness
        web_multiplier = Decimal("1")
        flange_multiplier = Decimal("2")
    elif kind == "BBH":
        # Variable-height BH: the tapered web is treated as a rectangle at the
        # mean of the two end heights, so the shop plate area is conserved.
        effective_height = (
            height
            if secondary_height is None
            else (height + secondary_height) / Decimal("2")
        )
        web_width = effective_height - Decimal("2") * flange_thickness
        web_multiplier = Decimal("1")
        flange_multiplier = Decimal("2")
    else:
        raise FabricatedProfileError(f"unsupported fabricated profile: {kind}")
    if min(height, width, web_thickness, flange_thickness, web_width) <= 0:
        raise FabricatedProfileError(
            f"fabricated profile has non-positive geometry: {kind}"
        )
    return (
        FabricatedChildGeometry(
            part_type=f"{kind}腹",
            thickness=web_thickness,
            width=web_width,
            quantity_multiplier=web_multiplier,
            is_main=True,
        ),
        FabricatedChildGeometry(
            part_type=f"{kind}翼",
            thickness=flange_thickness,
            width=width,
            quantity_multiplier=flange_multiplier,
            is_main=False,
        ),
    )


@dataclass(frozen=True, slots=True)
class FabricatedProfile:
    kind: str
    height: Decimal
    width: Decimal
    web_thickness: Decimal
    flange_thickness: Decimal
    secondary_height: Decimal | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"BH", "BOX", "BT", "BBH"}:
            raise FabricatedProfileError(
                f"unsupported fabricated profile: {self.kind}"
            )
        if self.secondary_height is not None and self.kind != "BBH":
            raise FabricatedProfileError(
                f"only BBH supports a variable height, not {self.kind}"
            )
        _split_geometry(
            self.kind,
            self.height,
            self.width,
            self.web_thickness,
            self.flange_thickness,
            self.secondary_height,
        )

    @property
    def mean_height(self) -> Decimal:
        """Section height used for the shop-plate web: mean of both ends."""
        if self.secondary_height is None:
            return self.height
        return (self.height + self.secondary_height) / Decimal("2")

    @property
    def normalized_spec(self) -> str:
        height = _number_text(self.height)
        if self.kind == "BBH" and self.secondary_height is not None:
            height = f"{height}-{_number_text(self.secondary_height)}"
        return (
            f"{self.kind}{height}*{_number_text(self.width)}"
            f"*{_number_text(self.web_thickness)}"
            f"*{_number_text(self.flange_thickness)}"
        )

    def children(
        self,
    ) -> tuple[FabricatedChildGeometry, FabricatedChildGeometry]:
        return _split_geometry(
            self.kind,
            self.height,
            self.width,
            self.web_thickness,
            self.flange_thickness,
            self.secondary_height,
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
    if not compact.startswith(("BBH", "BH", "BOX", "BT")):
        return None
    match = _FABRICATED.fullmatch(compact)
    if match is None:
        raise FabricatedProfileError(
            f"fabricated-profile specification is invalid: {compact!r}"
        )
    kind = match.group("kind")
    height = _decimal(match.group("height"))
    secondary_text = match.group("secondary")
    secondary = (
        _decimal(secondary_text) if secondary_text is not None else None
    )
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
    return FabricatedProfile(kind, height, width, web, flange, secondary)
