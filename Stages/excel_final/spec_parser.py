"""Pure, material-aware classification and dimension parsing for Excel Final."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from fabricated_profile import FabricatedProfileError, parse_fabricated_profile


class HandbookCategory(StrEnum):
    FLAT_STEEL = "flat_steel"
    ROUND_BAR = "round_bar"
    REBAR = "rebar"
    SQUARE_BAR = "square_bar"
    I_BEAM = "i_beam"
    H_BEAM = "h_beam"
    T_BEAM = "t_beam"
    CHANNEL = "channel"
    ANGLE = "angle"
    STEEL_PIPE = "steel_pipe"
    SQUARE_TUBE = "square_tube"
    HFW_PIPE = "hfw_pipe"
    W_BEAM = "w_beam"


class LookupPolicy(StrEnum):
    HANDBOOK = "handbook"
    PLATE_CONSTANT = "plate_constant"
    FLAT_THEN_PLATE = "flat_then_plate"
    SKIP = "skip"
    NOT_FOUND = "not_found"


class SplitPolicy(StrEnum):
    NONE = "none"
    BH = "BH"
    BOX = "BOX"
    BT = "BT"


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    original_spec: str
    normalized_type: str
    normalized_spec: str
    normalized_width: Decimal | None
    handbook_category: HandbookCategory | None
    lookup_policy: LookupPolicy
    split_policy: SplitPolicy
    reason: str | None = None


_NUMBER = r"\d+(?:\.\d+)?"
_EXPLICIT_PLATE_RE = re.compile(rf"^(?:PL|-)({_NUMBER})\*({_NUMBER})$", re.IGNORECASE)
_BARE_DIMENSIONS_RE = re.compile(rf"^({_NUMBER})\*({_NUMBER})$")
_EXPLICIT_FLAT_RE = re.compile(
    rf"^(?:FB|FLAT|扁钢|扁铁)({_NUMBER})\*({_NUMBER})$",
    re.IGNORECASE,
)
_D_BAR_RE = re.compile(rf"^D({_NUMBER})$", re.IGNORECASE)


def _compact(value: object) -> str:
    compact = str(value or "").replace(" ", "").replace("　", "")
    return re.sub(r"(?<=\d)[xX×](?=\d)", "*", compact)


def _number(value: str | Decimal | float | int) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid numeric dimension: {value!r}") from exc


def _number_text(value: str | Decimal | float | int) -> str:
    number = _number(value)
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _result(
    original_spec: str,
    *,
    normalized_type: str,
    normalized_spec: str,
    normalized_width: Decimal | None = None,
    category: HandbookCategory | None = None,
    lookup: LookupPolicy,
    split: SplitPolicy = SplitPolicy.NONE,
    reason: str | None = None,
) -> ClassificationResult:
    return ClassificationResult(
        original_spec=original_spec,
        normalized_type=normalized_type,
        normalized_spec=normalized_spec,
        normalized_width=normalized_width,
        handbook_category=category,
        lookup_policy=lookup,
        split_policy=split,
        reason=reason,
    )


def _handbook_profile(
    original_spec: str,
    normalized_spec: str,
    normalized_type: str,
    category: HandbookCategory,
) -> ClassificationResult:
    return _result(
        original_spec,
        normalized_type=normalized_type,
        normalized_spec=normalized_spec,
        category=category,
        lookup=LookupPolicy.HANDBOOK,
    )


def classify_normalized_spec(
    spec: object,
    *,
    material: object = "",
    width: str | Decimal | float | int | None = None,
    part_no: object = "",
) -> ClassificationResult:
    """Return one deterministic classification decision without querying the handbook."""
    original_spec = str(spec or "")
    compact = _compact(spec)
    part_key = _compact(part_no)
    if not compact and (
        part_key.startswith(
            ("NUT", "螺母", "SLEEVE", "螺套", "套筒", "TT", "BOLT", "螺栓")
        )
        or re.match(r"^M\d", part_key)
    ):
        compact = part_key
    upper = compact.upper()
    material_upper = _compact(material).upper()

    if width is not None and re.fullmatch(_NUMBER, compact):
        thickness = _number_text(compact)
        width_text = _number_text(width)
        if thickness == "6" and width_text == "30":
            return _handbook_profile(
                original_spec,
                "6*30",
                "扁钢",
                HandbookCategory.FLAT_STEEL,
            )
        return _result(
            original_spec,
            normalized_type="板材",
            normalized_spec=thickness,
            normalized_width=_number(width),
            lookup=LookupPolicy.PLATE_CONSTANT,
        )

    if upper.startswith("NUT") or upper.startswith("螺母"):
        return _result(
            original_spec,
            normalized_type="螺母",
            normalized_spec=upper,
            lookup=LookupPolicy.SKIP,
        )
    if upper.startswith(("SLEEVE", "螺套", "套筒")):
        return _result(
            original_spec,
            normalized_type="螺套",
            normalized_spec=upper,
            lookup=LookupPolicy.SKIP,
        )
    if upper.startswith("TT"):
        return _result(
            original_spec,
            normalized_type="TT",
            normalized_spec=upper,
            lookup=LookupPolicy.SKIP,
        )
    if re.match(r"^(?:M\d|BOLT|螺栓|TS\d|HS\d)", upper):
        return _result(
            original_spec,
            normalized_type="螺栓",
            normalized_spec=upper,
            lookup=LookupPolicy.SKIP,
        )

    match = _EXPLICIT_PLATE_RE.fullmatch(upper)
    if match:
        thickness = _number_text(match.group(1))
        plate_width = _number_text(match.group(2))
        if thickness == "6" and plate_width == "30":
            return _handbook_profile(
                original_spec,
                "6*30",
                "扁钢",
                HandbookCategory.FLAT_STEEL,
            )
        return _result(
            original_spec,
            normalized_type="板材",
            normalized_spec=thickness,
            normalized_width=_number(plate_width),
            lookup=LookupPolicy.PLATE_CONSTANT,
        )

    match = _EXPLICIT_FLAT_RE.fullmatch(upper)
    if match:
        normalized = f"{_number_text(match.group(1))}*{_number_text(match.group(2))}"
        return _handbook_profile(
            original_spec,
            normalized,
            "扁钢",
            HandbookCategory.FLAT_STEEL,
        )

    match = _BARE_DIMENSIONS_RE.fullmatch(upper)
    if match:
        normalized = f"{_number_text(match.group(1))}*{_number_text(match.group(2))}"
        return _result(
            original_spec,
            normalized_type="扁钢候选",
            normalized_spec=normalized,
            category=HandbookCategory.FLAT_STEEL,
            lookup=LookupPolicy.FLAT_THEN_PLATE,
        )

    for prefix, split in (("BOX", SplitPolicy.BOX), ("BH", SplitPolicy.BH), ("BT", SplitPolicy.BT)):
        if upper.startswith(prefix):
            try:
                fabricated = parse_fabricated_profile(upper)
            except FabricatedProfileError as exc:
                normalized = upper
                reason = str(exc)
            else:
                normalized = fabricated.normalized_spec if fabricated is not None else upper
                reason = None
            return _result(
                original_spec,
                normalized_type=prefix,
                normalized_spec=normalized,
                lookup=LookupPolicy.PLATE_CONSTANT,
                split=split,
                reason=reason,
            )

    if upper.startswith("HA"):
        return _result(
            original_spec,
            normalized_type="未分类",
            normalized_spec=upper,
            lookup=LookupPolicy.NOT_FOUND,
            reason="HA不在支持范围",
        )

    match = _D_BAR_RE.fullmatch(upper)
    if match:
        diameter = _number_text(match.group(1))
        if material_upper.startswith("HRB"):
            return _handbook_profile(
                original_spec,
                diameter,
                "螺纹钢",
                HandbookCategory.REBAR,
            )
        if material_upper.startswith(("HPB", "Q235B", "Q355B")):
            return _handbook_profile(
                original_spec,
                diameter,
                "圆钢",
                HandbookCategory.ROUND_BAR,
            )
        return _result(
            original_spec,
            normalized_type="未分类",
            normalized_spec=diameter,
            lookup=LookupPolicy.NOT_FOUND,
            reason="D系列材质不足",
        )

    if re.match(r"^(?:I|HI)\d", upper):
        return _handbook_profile(original_spec, upper, "工字钢", HandbookCategory.I_BEAM)
    if re.match(r"^(?:LH|HFW)\d", upper):
        return _handbook_profile(original_spec, upper, "高频焊", HandbookCategory.HFW_PIPE)
    if re.match(r"^(?:HN|HW|HM|HT)\d", upper) or re.match(r"^H\d", upper):
        return _handbook_profile(original_spec, upper, "H型钢", HandbookCategory.H_BEAM)
    if re.match(r"^(?:TN|TW|TM|T)\d", upper):
        return _handbook_profile(original_spec, upper, "T型钢", HandbookCategory.T_BEAM)
    if re.match(r"^(?:C\d|\[)", upper):
        return _handbook_profile(original_spec, upper, "槽钢", HandbookCategory.CHANNEL)
    if re.match(r"^(?:L\d|∠)", upper):
        return _handbook_profile(original_spec, upper, "角钢", HandbookCategory.ANGLE)
    if upper.startswith(("方管", "矩形管", "□")):
        return _handbook_profile(original_spec, upper, "方管", HandbookCategory.SQUARE_TUBE)
    if re.match(r"^(?:PIP|IP|P|Φ|D)\d+(?:\.\d+)?\*", upper):
        return _handbook_profile(original_spec, upper, "钢管", HandbookCategory.STEEL_PIPE)
    if upper.startswith("方钢"):
        return _handbook_profile(original_spec, upper, "方钢", HandbookCategory.SQUARE_BAR)
    if re.match(r"^W\d", upper):
        return _handbook_profile(original_spec, upper, "W型钢", HandbookCategory.W_BEAM)

    return _result(
        original_spec,
        normalized_type="未分类",
        normalized_spec=upper,
        lookup=LookupPolicy.NOT_FOUND,
        reason="无法分类规格",
    )


def classify_spec(spec: str, material: str = "") -> str:
    """Compatibility label for legacy callers while the canonical path migrates."""
    result = classify_normalized_spec(spec, material=material)
    if result.split_policy is not SplitPolicy.NONE:
        return result.split_policy.value
    if result.normalized_type == "工字钢":
        return "I"
    if result.normalized_type in {"板材", "扁钢", "扁钢候选"}:
        return "PL"
    if result.normalized_type in {"圆钢", "螺纹钢"}:
        return "D"
    if result.normalized_type == "螺栓":
        return "M20"
    return "UNKNOWN"


def parse_plate_dims(spec: str) -> tuple[float, float] | None:
    """Parse explicit or bare two-number dimensions in their written order."""
    compact = _compact(spec).upper()
    match = _EXPLICIT_PLATE_RE.fullmatch(compact) or _BARE_DIMENSIONS_RE.fullmatch(compact)
    if match is None:
        return None
    return (float(match.group(1)), float(match.group(2)))
