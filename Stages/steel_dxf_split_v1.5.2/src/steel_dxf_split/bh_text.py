from __future__ import annotations

import re

import ezdxf

from .dxf_io import normalize_text, recursive_virtual_entities

_H_RE = re.compile(
    r"(?<![A-Z0-9])(?P<family>BH)\s*"
    r"(?P<h1>\d+(?:\.\d+)?)"
    r"(?:\s*[-~～—]\s*(?P<h2>\d+(?:\.\d+)?))?\s*[xX×*]\s*"
    r"(?P<b>\d+(?:\.\d+)?)\s*[xX×*]\s*"
    r"(?P<tw>\d+(?:\.\d+)?)\s*[xX×*]\s*"
    r"(?P<tf>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_SCALE_RE = re.compile(r"(?<!\d)1\s*:\s*(?P<scale>\d+(?:\.\d+)?)")
_PART_RE = re.compile(r"(?i)^[a-z0-9]+(?:-[a-z0-9]+)+$")
_MATERIAL_RE = re.compile(r"(?i)^Q\d{3}[A-Z0-9-]*$")
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")


def detect_profile_family(doc: ezdxf.document.Drawing) -> str | None:
    for entity in doc.modelspace():
        items = recursive_virtual_entities(entity) if entity.dxftype() == "INSERT" else [entity]
        for item in items:
            if item.dxftype() != "TEXT":
                continue
            text = normalize_text(item.dxf.text)
            match = _H_RE.search(text)
            if match:
                return "BH"
    return None

def canonical_bh_label(part_number: str, role: str, index: int | None = None, quantity: int = 1) -> str:
    if role == "web":
        base = f"p={part_number}腹"
    elif role == "flange":
        flange_role = {1: "上翼", 2: "下翼"}.get(index, "翼")
        base = f"p={part_number}{flange_role}"
    else:
        raise ValueError(f"Unsupported BH role: {role}")
    if index is not None and (role != "flange" or index not in {1, 2}):
        base += f"-{index}"
    return base
