from steel_dxf_classifier.model import Disposition, ProfileParse, TextFact


def test_profile_parse_serializes_xbox_evidence() -> None:
    profile = ProfileParse(
        raw="HK300-10-15*200-25",
        normalized="XBOX300*200*10*15*25",
        part_type="XBOX",
        catalog_status="registered",
        type_source="catalog",
        profile_source_dialect="HK",
        profile_extra=25.0,
    )

    assert profile.to_dict()["profile_source_dialect"] == "HK"
    assert profile.to_dict()["profile_extra"] == 25.0


def test_text_fact_serializes_source_evidence() -> None:
    fact = TextFact(
        raw="BH300*200*6*8",
        normalized="BH300*200*6*8",
        x=10.0,
        y=20.0,
        height=3.0,
        entity_type="TEXT",
        layer="Other",
        handle="2A",
        block_path=("FRAME",),
    )

    assert fact.to_dict()["block_path"] == ["FRAME"]
    assert Disposition.CLASSIFIED.value == "classified"
