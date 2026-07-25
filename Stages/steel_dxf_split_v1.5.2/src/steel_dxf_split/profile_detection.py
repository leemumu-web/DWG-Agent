from __future__ import annotations

import re
from typing import cast

import ezdxf
from ezdxf.entities import Insert, MText, Text

from .dxf_io import normalize_text, recursive_virtual_entities


_BH_PROFILE = re.compile(
    r"(?<![A-Z0-9])(?P<family>BH|WH|HW|HM|HN|H)\s*"
    r"(?P<h1>\d+(?:\.\d+)?)"
    r"(?:\s*[-~～]\s*(?P<h2>\d+(?:\.\d+)?))?\s*[xX×*]\s*"
    r"(?P<b>\d+(?:\.\d+)?)\s*[xX×*]\s*"
    r"(?P<tw>\d+(?:\.\d+)?)\s*[xX×*]\s*"
    r"(?P<tf>\d+(?:\.\d+)?)(?![A-Z0-9])",
    re.IGNORECASE,
)
_BOX_PROFILE = re.compile(
    r"\bBOX\s*\d+(?:\.\d+)?\s*[*X×]\s*\d+(?:\.\d+)?"
    r"\s*[*X×]\s*\d+(?:\.\d+)?\s*[*X×]\s*\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)


class ProfileFamilyConflictError(ValueError):
    """A drawing contains evidence for more than one supported family."""


def detect_profile_family(doc: ezdxf.document.Drawing) -> str | None:
    """Return one unique top-level profile family after scanning all text."""

    families: set[str] = set()
    for entity in doc.modelspace():
        expanded = (
            recursive_virtual_entities(cast(Insert, entity))
            if entity.dxftype() == "INSERT"
            else (entity,)
        )
        for item in expanded:
            if item.dxftype() not in {"TEXT", "MTEXT"}:
                continue
            raw = (
                cast(Text, item).dxf.text
                if item.dxftype() == "TEXT"
                else cast(MText, item).plain_text()
            )
            value = normalize_text(str(raw))
            if _BOX_PROFILE.search(value):
                families.add("BOX")
            if _BH_PROFILE.search(value):
                families.add("BH")
    if len(families) > 1:
        raise ProfileFamilyConflictError(
            "检测到冲突的型材族证据：" + ", ".join(sorted(families))
        )
    return next(iter(families), None)
