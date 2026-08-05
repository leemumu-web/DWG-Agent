r"""Tests for decoder.py — GBK \M+5XXXX decoding."""

import pytest
from dxf2excel.decoder import decode_m5


# Verified data: raw \M+5 strings → expected Chinese text
DECODE_TEST_CASES = [
    ("\\M+5C1E3\\M+5BCFE\\M+5BAC5", "零件号"),
    ("\\M+5BDD8\\M+5C3E6\\M+5D0CD\\M+5B2C4", "截面型材"),
    ("\\M+5B3A4\\M+5B6C8(mm)", "长度(mm)"),
    ("\\M+5B2C4\\M+5D6CA", "材质"),
    ("\\M+5CAFD\\M+5C1BF", "数量"),
    ("\\M+5B5A5\\M+5D6D8(kg)", "单重(kg)"),
    ("\\M+5D7DC\\M+5D6D8(kg)", "总重(kg)"),
    ("\\M+5D7DC\\M+5C3E6\\M+5BBFD(m2)", "总面积(m2)"),
    ("\\M+5B1B8\\M+5D7A2", "备注"),
    ("\\M+5B9B9\\M+5BCFE\\M+5CAFD\\M+5C1BF\\M+5A3BA", "构件数量："),
    ("\\M+5B9B9\\M+5BCFE\\M+5D7DC\\M+5D6D8\\M+5A3BA", "构件总重："),
    ("\\M+5B2C4  \\M+5C1CF  \\M+5B1ED", "材  料  表"),
]


@pytest.mark.parametrize("raw,expected", DECODE_TEST_CASES)
def test_decode_known_headers(raw: str, expected: str) -> None:
    """All 9+ known header strings decode correctly."""
    result = decode_m5(raw)
    assert result == expected, f"Expected '{expected}', got '{result}'"


def test_decode_mixed_ascii() -> None:
    r"""Mixed ASCII and \M+5 sequences pass through correctly."""
    result = decode_m5("b7-b-1")
    assert result == "b7-b-1"


def test_decode_empty() -> None:
    """Empty string returns empty."""
    assert decode_m5("") == ""


def test_decode_no_m5_pattern() -> None:
    r"""Text without \M+5 remains unchanged."""
    assert decode_m5("PL10*135") == "PL10*135"
    assert decode_m5("Q355B") == "Q355B"
    assert decode_m5("3380.85") == "3380.85"


def test_decode_invalid_hex() -> None:
    """Invalid hex sequences are kept as-is."""
    result = decode_m5("\\M+5GGGG")
    assert "\\M+5GGGG" in result


def test_decode_partial_hex() -> None:
    """Partial/incomplete sequences pass through."""
    result = decode_m5("\\M+5AB")
    assert "\\M+5AB" in result


def test_header_section_spec_alias_maps_to_spec() -> None:
    from dxf2excel.config import HEADER_ALIASES
    from dxf2excel.text_normalizer import header_to_field_key

    assert "截面规格" in HEADER_ALIASES
    assert header_to_field_key("截面规格") == "spec"
