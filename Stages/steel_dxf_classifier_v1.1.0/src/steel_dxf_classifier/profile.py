from __future__ import annotations

import re

from .model import ProfileParse
from .text import normalize_text


REGISTERED_TYPES = frozenset(
    {
        "PL", "FB", "FL", "BL",
        "BH", "BBH", "RH", "BOX", "XBOX", "BT", "PX",
        "H", "HW", "HM", "HN", "HT", "HE", "HEA", "HEB", "HEM", "HL", "HD", "HP",
        "I", "IPE", "IPN", "INP", "UB", "UC", "W", "S", "M", "T", "WT", "ST", "MT",
        "L", "C", "CH", "PFC", "MC", "U", "UPN", "UPE", "Z",
        "RHS", "SHS", "CHS", "HSS", "PIPE",
        "RB", "SB",
    }
)

_PREFIX_RE = re.compile(r"^([A-Z]{1,12})(.*)$")
_DIMENSION_BODY_RE = re.compile(r"^[0-9][0-9.*+()\-/ ]*$")
_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)"
_BOX5_RE = re.compile(
    rf"^BOX\s*({_NUMBER})\s*[*X×]\s*({_NUMBER})\s*[*X×]\s*({_NUMBER})"
    rf"\s*[*X×]\s*({_NUMBER})\s*[*X×]\s*({_NUMBER})$",
    re.IGNORECASE,
)
_HK_RE = re.compile(
    rf"^HK\s*({_NUMBER})\s*-\s*({_NUMBER})\s*-\s*({_NUMBER})"
    rf"\s*[*X×]\s*({_NUMBER})\s*-\s*({_NUMBER})$",
    re.IGNORECASE,
)


def _format_dimension(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.12g}"


def _parse_xbox_business_profile(raw: str, normalized: str) -> ProfileParse | None:
    match = _BOX5_RE.fullmatch(normalized)
    dialect = "BOX5"
    if match is None:
        match = _HK_RE.fullmatch(normalized)
        dialect = "HK"
    if match is None:
        return None
    values = tuple(float(part) for part in match.groups())
    if dialect == "BOX5":
        height, width, web, flange, extra = values
    else:
        height, web, flange, width, extra = values
    if (
        min(height, width, web, flange, extra) <= 0.0
        or height - 2.0 * flange <= 0.0
        or width - 2.0 * web <= 0.0
    ):
        return None
    canonical = "XBOX" + "*".join(
        _format_dimension(value)
        for value in (height, width, web, flange, extra)
    )
    return ProfileParse(
        raw=raw,
        normalized=canonical,
        part_type="XBOX",
        catalog_status="registered",
        type_source="catalog",
        profile_source_dialect=dialect,
        profile_extra=extra,
    )


def parse_profile(raw: str) -> ProfileParse | None:
    normalized = normalize_text(raw).upper()
    if not normalized or any(token in normalized for token in ("..", "/", "\\")):
        return None

    xbox_profile = _parse_xbox_business_profile(raw, normalized)
    if xbox_profile is not None:
        return xbox_profile
    if _BOX5_RE.fullmatch(normalized) is not None or _HK_RE.fullmatch(normalized) is not None:
        return None

    match = _PREFIX_RE.fullmatch(normalized)
    if match is None:
        return None
    prefix, body = match.groups()
    body = body.strip().replace(" X ", "*").replace("X", "*")
    body = re.sub(r"\s*\*\s*", "*", body)
    body = body.replace(" ", "")
    if not body or _DIMENSION_BODY_RE.fullmatch(body) is None:
        return None
    if not any(character.isdigit() for character in body):
        return None
    if prefix == "M" and "*" not in body:
        return None

    registered = prefix in REGISTERED_TYPES
    if not registered and len(prefix) < 2:
        return None
    canonical = f"{prefix}{body}"
    return ProfileParse(
        raw=raw,
        normalized=canonical,
        part_type=prefix,
        catalog_status="registered" if registered else "unregistered",
        type_source="catalog" if registered else "auto_discovered",
    )
