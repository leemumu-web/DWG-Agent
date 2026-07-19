from steel_dxf_classifier.model import Disposition, TextFact


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
