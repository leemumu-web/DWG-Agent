import pytest

from steel_dxf_classifier.profile import REGISTERED_TYPES, parse_profile


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("BH1500*500*30*30", "BH"),
        ("BBH600*200*12*22", "BBH"),
        (" box600×600×30×30 ", "BOX"),
        ("XBOX300*300*10*10", "XBOX"),
        ("ＰＬ 20＊300", "PL"),
        ("HN400*200*8*13", "HN"),
        ("RHS200*100*8", "RHS"),
        ("L90*8", "L"),
        ("TT25", "TT"),
        ("pipe 219 x 8", "PIPE"),
    ],
)
def test_parse_profile_preserves_concrete_prefix(raw: str, kind: str) -> None:
    parsed = parse_profile(raw)

    assert parsed is not None
    assert parsed.part_type == kind
    assert parsed.catalog_status == (
        "registered" if kind in REGISTERED_TYPES else "unregistered"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "400*200*8*13",
        "Q355B",
        "1:10",
        "../BH300",
        "BH",
        "BH尺寸见图",
        "",
    ],
)
def test_parse_profile_rejects_non_profile_or_unsafe_values(raw: str) -> None:
    assert parse_profile(raw) is None


def test_registered_taxonomy_keeps_specific_engineering_prefixes() -> None:
    assert {
        "PL", "FB", "BH", "BBH", "BOX", "XBOX", "BT", "H", "HW", "HM", "HN",
        "HEA", "HEB", "HEM", "I", "IPE", "IPN", "UB", "UC", "T",
        "L", "C", "CH", "PFC", "U", "Z", "RHS", "SHS", "CHS",
        "PIPE", "RB", "SB",
    } <= REGISTERED_TYPES
