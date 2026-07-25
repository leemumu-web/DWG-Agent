from __future__ import annotations


def dimension_property_type(scope: str, orientation: str) -> str:
    """Map a proven Tekla relation to a BH property under the horizontal-X contract."""

    if scope == "pitch_chain":
        return "equal_pitch_chain"
    if scope != "view_extent":
        return "unresolved"
    if orientation == "horizontal":
        return "longitudinal_extent"
    if orientation == "vertical":
        return "transverse_envelope"
    return "unresolved"


def displayed_dimension_tolerance(
    text: str,
    *,
    geometric_tolerance_mm: float = 0.15,
) -> float:
    """Return the uncertainty carried by a displayed decimal dimension.

    A dimension rendered as an integer has already discarded up to half a
    millimetre through display rounding.  Treating its text as exact either
    loses real Tekla dimension chains or encourages a length-dependent fuzzy
    tolerance.  This function keeps the uncertainty tied to the declared
    precision of the drawing instead.
    """

    normalized = text.strip()
    decimals = len(normalized.rsplit(".", 1)[1]) if "." in normalized else 0
    display_quantum_mm = 10.0 ** (-decimals)
    return float(geometric_tolerance_mm) + display_quantum_mm / 2.0
