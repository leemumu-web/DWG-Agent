from __future__ import annotations

import pytest

from steel_dxf_split import bh_annotations
from steel_dxf_split.dxf_io import decode_cad_text_transport, normalize_text


def test_transport_decoder_decodes_general_mif_chinese_without_stripping_cad_formatting() -> None:
    assert (
        decode_cad_text_transport(r"{\H2;\M+5C1E3\M+5BCFE}\P16\M+5A6B522")
        == "{\\H2;零件}\\P16Φ22"
    )


def test_transport_decoder_leaves_invalid_mif_auditable() -> None:
    assert decode_cad_text_transport(r"16\M+9A6B522") == r"16\M+9A6B522"


def test_normalize_text_decodes_mif_encoded_diameter_symbol() -> None:
    assert normalize_text(r"16\M+5A6B522") == "16Φ22"


def test_normalize_text_canonicalizes_legacy_cp936_diameter_mojibake() -> None:
    assert normalize_text("32¦µ26") == "32Φ26"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (r"16\M+5A6B522", (16, 22.0)),
        ("16Φ22", (16, 22.0)),
        ("16%%c22", (16, 22.0)),
        ("16-Ø22", (16, 22.0)),
        ("16个⌀22孔", (16, 22.0)),
        ("4-M20", (4, 20.0)),
        ("32¦µ26", (32, 26.0)),
        ("18*D22(22x35)", (18, 22.0)),
    ],
)
def test_parse_bolt_mark_text_requires_an_explicit_diameter_semantic(
    text: str,
    expected: tuple[int, float],
) -> None:
    assert bh_annotations.parse_bolt_mark_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        r"16\M+9A6B522",
        r"16\M+5ZZZZ22",
        "16-22",
        "16 foo 5",
        "16x100",
        "16*D22",
    ],
)
def test_parse_bolt_mark_text_rejects_unresolved_or_ambiguous_text(text: str) -> None:
    assert bh_annotations.parse_bolt_mark_text(text) is None
