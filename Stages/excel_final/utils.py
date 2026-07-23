"""Small cell-value conversion helpers shared by input adapters."""

from __future__ import annotations

import math
from typing import Any


def safe_float(value: Any) -> float | None:
    """Convert a cell value to float, returning ``None`` when it is absent/invalid."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip()
            if value in {"", "-"}:
                return None
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_str(value: Any) -> str:
    """Convert a cell value to a stripped string; NaN and ``None`` become empty."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()
