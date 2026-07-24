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


def parse_profile(raw: str) -> ProfileParse | None:
    normalized = normalize_text(raw).upper()
    if not normalized or any(token in normalized for token in ("..", "/", "\\")):
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
