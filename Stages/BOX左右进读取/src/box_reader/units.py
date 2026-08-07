from __future__ import annotations

_INSUNITS: dict[int, tuple[str, float | None]] = {
    0: ("unitless", None), 1: ("inch", 25.4), 2: ("foot", 304.8),
    3: ("mile", 1_609_344.0), 4: ("millimetre", 1.0),
    5: ("centimetre", 10.0), 6: ("metre", 1000.0),
    7: ("kilometre", 1_000_000.0), 8: ("microinch", 0.0000254),
    9: ("mil", 0.0254), 10: ("yard", 914.4), 11: ("angstrom", 1e-7),
    12: ("nanometre", 1e-6), 13: ("micron", 0.001),
    14: ("decimetre", 100.0), 15: ("decametre", 10_000.0),
    16: ("hectometre", 100_000.0), 17: ("gigametre", 1e12),
    18: ("astronomical_unit", 149_597_870_700_000.0),
    19: ("light_year", 9.4607304725808e18),
    20: ("parsec", 3.085677581491367e21),
    21: ("us_survey_foot", 304.8006096012192),
    22: ("us_survey_inch", 25.4000508001016),
    23: ("us_survey_yard", 914.4018288036576),
    24: ("us_survey_mile", 1_609_347.2186944373),
}


def insunits_info(code: int | None) -> tuple[str, float | None]:
    if code is None:
        return "missing", None
    return _INSUNITS.get(code, (f"unknown_{code}", None))
