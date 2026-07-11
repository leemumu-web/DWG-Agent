"""Specification string classification and dimension parsing.

Shared by both the Tekla TSV pipeline and the initial table pipeline.
Pure regex — zero project dependencies.
"""

from __future__ import annotations

import re

# Special density for D8 rebar
D8_DENSITY = 0.395  # kg/m

# ── Regex patterns ───────────────────────────────────────────────

# Two-number plate specs: PL10*143 or -15*3000
_PLATE_RE = re.compile(r"^(?:PL|-)\s*([\d.]+)\s*\*\s*([\d.]+)", re.IGNORECASE)

# Bare two-number specs: 10*143 (no prefix)
_BARE_PLATE_RE = re.compile(r"^([\d.]+)\s*\*\s*([\d.]+)")

# D8 rebar (exact match)
_D8_RE = re.compile(r"^D8$", re.IGNORECASE)

# D19/D22 studs (D15-D29 range)
_DSTUD_RE = re.compile(r"^D(1[5-9]|2\d)$", re.IGNORECASE)

# M20/M24/M30 bolts
_MBOLT_RE = re.compile(r"^M\d+", re.IGNORECASE)

# ── Public API ───────────────────────────────────────────────────


def classify_spec(spec: str) -> str:
    """Classify a spec string into a profile type.

    Returns one of:
      'M20'   — bolt (M prefix + digits)
      'D8'    — 8mm rebar
      'D19'   — stud (D15-D29)
      'BH'    — welded H-beam (BH/HA prefix)
      'BOX'   — box section (BOX prefix)
      'BT'    — T-beam (BT prefix)
      'I'     — I-beam (I/HI prefix)
      'PL'    — plate (PL/- prefix or bare <num>*<num>)
      'UNKNOWN'
    """
    if not spec or not isinstance(spec, str):
        return "UNKNOWN"
    s = spec.strip().upper()

    # Order matters: bolts before bare numbers
    if _MBOLT_RE.match(s):
        return "M20"  # canonical bolt type
    if _D8_RE.match(s):
        return "D8"
    if _DSTUD_RE.match(s):
        return "D19"  # canonical stud type
    if s.startswith("BH") or s.startswith("HA"):
        return "BH"
    if s.startswith("BOX"):
        return "BOX"
    if s.startswith("BT"):
        return "BT"
    if s.startswith("I") or s.startswith("HI"):
        return "I"
    if s.startswith("PL") or s.startswith("-"):
        return "PL"
    if _BARE_PLATE_RE.match(s):
        return "PL"

    return "UNKNOWN"


def parse_plate_dims(spec: str) -> tuple[float, float] | None:
    """Parse a plate spec into (thickness, width), sorted smaller-first.

    Handles: PL10*143, -15*3000, and bare 10*143.
    Returns None if the spec doesn't match.
    """
    s = str(spec).strip()
    # Try prefixed first
    m = _PLATE_RE.match(s)
    if not m:
        m = _BARE_PLATE_RE.match(s)
    if not m:
        return None
    try:
        a, b = float(m.group(1)), float(m.group(2))
    except (ValueError, IndexError):
        return None
    # Sort: smaller = thickness, larger = width
    if a > b:
        return (b, a)
    return (a, b)
