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
        ("PX300*150*8", "PX"),
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
    assert parsed.type_source == (
        "catalog" if kind in REGISTERED_TYPES else "auto_discovered"
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
        "A300",
        "M20",
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
        "PX",
    } <= REGISTERED_TYPES


@pytest.mark.parametrize("kind", sorted(REGISTERED_TYPES))
def test_every_catalog_type_has_a_parseable_dimension_example(kind: str) -> None:
    parsed = parse_profile(f"{kind}100*50*5")

    assert parsed is not None
    assert parsed.part_type == kind
    assert parsed.type_source == "catalog"


def test_safe_unknown_multi_letter_prefix_is_auto_discovered() -> None:
    parsed = parse_profile("XY250*120*8")

    assert parsed is not None
    assert parsed.part_type == "XY"
    assert parsed.normalized == "XY250*120*8"
    assert parsed.type_source == "auto_discovered"


@pytest.mark.parametrize(
    ("raw", "normalized", "dialect", "extra"),
    [
        ("BOX300*500*50*30*50", "XBOX300*500*50*30*50", "BOX5", 50.0),
        ("HK300-10-15*200-25", "XBOX300*200*10*15*25", "HK", 25.0),
        (" hk 500 - 20 - 25 * 500 - 30 ", "XBOX500*500*20*25*30", "HK", 30.0),
    ],
)
def test_xbox_business_dialects_normalize_to_xbox(
    raw: str,
    normalized: str,
    dialect: str,
    extra: float,
) -> None:
    parsed = parse_profile(raw)

    assert parsed is not None
    assert parsed.part_type == "XBOX"
    assert parsed.normalized == normalized
    assert parsed.profile_source_dialect == dialect
    assert parsed.profile_extra == extra
    assert parsed.type_source == "catalog"


def test_four_dimension_box_remains_box() -> None:
    parsed = parse_profile("BOX300*500*50*30")

    assert parsed is not None
    assert parsed.part_type == "BOX"
    assert parsed.normalized == "BOX300*500*50*30"
    assert parsed.profile_source_dialect is None
    assert parsed.profile_extra is None


@pytest.mark.parametrize(
    "raw",
    [
        "BOX50*500*50*30*50",
        "BOX300*80*50*30*50",
        "BOX300*500*50*30*0",
        "HK30-10-15*200-25",
        "HK300-100-15*200-25",
        "HK300-10-15*200-0",
    ],
)
def test_invalid_xbox_business_profile_is_rejected(raw: str) -> None:
    assert parse_profile(raw) is None
