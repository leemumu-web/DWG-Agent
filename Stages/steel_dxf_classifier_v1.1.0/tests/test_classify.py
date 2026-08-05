from pathlib import Path

from steel_dxf_classifier.classify import classify_facts, classify_file
from steel_dxf_classifier.model import Disposition, TextFact


def fact(
    text: str,
    x: float,
    y: float,
    *,
    block_path: tuple[str, ...] = ("TITLE",),
    height: float = 3.0,
) -> TextFact:
    return TextFact(text, text, x, y, height, "TEXT", "Other", None, block_path)


def test_unique_upper_right_section_value_is_classified() -> None:
    result = classify_facts(
        "a.dxf",
        [
            fact("截面", 80, 95),
            fact("BH300*200*6*8", 78, 85),
            fact("Q355B", 90, 85),
            fact("图纸", 0, 0, block_path=()),
        ],
    )

    assert result.disposition is Disposition.CLASSIFIED
    assert result.part_type == "BH"
    assert result.diagnostics == ("TITLE_PROFILE_PROVED",)
    assert result.candidates[0].value.normalized == "BH300*200*6*8"
    assert result.profile_raw == "BH300*200*6*8"
    assert result.profile_normalized == "BH300*200*6*8"
    assert result.type_source == "catalog"
    assert result.group_key == "type:BH"
    assert result.next_stage_eligible is True


def test_material_table_with_multiple_profile_rows_requires_review() -> None:
    rows = [
        fact("规格", 80, 95),
        fact("PL10*100", 80, 90),
        fact("PL12*200", 80, 85),
        fact("BOX300*300*10*10", 80, 80),
        fact("图纸", 0, 0, block_path=()),
    ]

    result = classify_facts("assembly.dxf", rows)

    assert result.disposition is Disposition.REVIEW_REQUIRED
    assert "TITLE_VALUE_CONFLICT" in result.diagnostics
    assert result.part_type is None
    assert result.group_key == "status:review_required"
    assert result.next_stage_eligible is False


def test_duplicate_profile_value_in_multiple_blocks_is_classified() -> None:
    # Real drawings repeat the same title-block text in several anonymous
    # blocks (*A5, *A7). Identical profile values are redundancy, not conflict.
    result = classify_facts(
        "dup-block.dxf",
        [
            fact("规格", 9140.0, 8167.0, block_path=("*A5",), height=50.0),
            fact("BOX700*400*30*30", 9037.4, 8067.0, block_path=("*A5",), height=50.0),
            fact("规格", 9140.0, 8167.0, block_path=("*A7",), height=50.0),
            fact("BOX700*400*30*30", 9037.4, 8067.0, block_path=("*A7",), height=50.0),
        ],
    )

    assert result.disposition is Disposition.CLASSIFIED
    assert result.part_type == "BOX"
    assert result.diagnostics == ("TITLE_PROFILE_PROVED",)
    assert result.profile_normalized == "BOX700*400*30*30"
    assert result.group_key == "type:BOX"
    assert result.next_stage_eligible is True


def test_section_spec_label_is_recognized() -> None:
    result = classify_facts(
        "section-spec.dxf",
        [
            fact("截面规格", 80, 95),
            fact("BOX700*400*30*30", 78, 85),
            fact("图纸", 0, 0, block_path=()),
        ],
    )

    assert result.disposition is Disposition.CLASSIFIED
    assert result.part_type == "BOX"
    assert result.profile_normalized == "BOX700*400*30*30"
    assert result.group_key == "type:BOX"
    assert result.next_stage_eligible is True


def test_lower_left_section_label_is_not_treated_as_title_block() -> None:
    result = classify_facts(
        "detail.dxf",
        [
            fact("截面", 10, 10),
            fact("BH300*200*6*8", 10, 5),
            fact("右上说明", 100, 100, block_path=()),
        ],
    )

    assert result.disposition is Disposition.REVIEW_REQUIRED
    assert result.diagnostics == ("TITLE_FIELD_MISSING",)


def test_same_row_profile_value_is_supported() -> None:
    result = classify_facts(
        "same-row.dxf",
        [fact("PROFILE", 70, 90), fact("RHS200*100*8", 85, 90), fact("x", 0, 0)],
    )

    assert result.disposition is Disposition.CLASSIFIED
    assert result.part_type == "RHS"
    assert result.candidates[0].direction == "right"


def test_strong_title_evidence_auto_discovers_safe_prefix() -> None:
    result = classify_facts(
        "custom.dxf",
        [fact("SECTION", 80, 95), fact("TT25", 80, 88), fact("x", 0, 0)],
    )

    assert result.disposition is Disposition.CLASSIFIED
    assert result.part_type == "TT"
    assert result.type_source == "auto_discovered"
    assert result.group_key == "type:TT"
    assert result.next_stage_eligible is True
    assert "PROFILE_TYPE_AUTO_DISCOVERED" in result.diagnostics


def test_missing_profile_value_requires_review() -> None:
    result = classify_facts("missing.dxf", [fact("截面", 80, 95), fact("Q355B", 80, 85)])

    assert result.disposition is Disposition.REVIEW_REQUIRED
    assert result.diagnostics == ("TITLE_VALUE_MISSING",)
    assert result.group_key == "status:review_required"
    assert result.next_stage_eligible is False


def test_corrupted_file_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "broken.dxf"
    path.write_text("not a dxf", encoding="utf-8")

    result = classify_file(path)

    assert result.disposition is Disposition.UNREADABLE
    assert result.diagnostics[0] == "DXF_READ_FAILED"
    assert result.group_key == "status:unreadable"
    assert result.next_stage_eligible is False
