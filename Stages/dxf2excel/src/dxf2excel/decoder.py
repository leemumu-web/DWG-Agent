r"""Decoder for CAD \M+5XXXX escape sequences → GBK Chinese characters.

The \M+5XXXX format is used by ZWCAD/AutoCAD BigFont to encode
double-byte characters from Chinese codepages.  Despite the "M" prefix
(originally for Big5), in these ANSI_936 files the 4-digit hex value
encodes a GBK codepoint directly.
"""

import re

_M5_PATTERN = re.compile(r"\\M\+5([0-9A-Fa-f]{4})")


def decode_m5(text: str) -> str:
    """Decode \\M+5XXXX escape sequences to GBK Chinese characters.

    Example:
        "\\\\M+5C1E3\\\\M+5BCFE\\\\M+5BAC5" → "零件号"

    Args:
        text: Raw DXF text possibly containing \\M+5XXXX sequences.

    Returns:
        Decoded text with all \\M+5XXXX replaced by Unicode characters.
    """

    def replacer(m: re.Match[str]) -> str:
        hex_val = int(m.group(1), 16)
        high = (hex_val >> 8) & 0xFF
        low = hex_val & 0xFF
        try:
            return bytes([high, low]).decode("gbk")
        except (UnicodeDecodeError, ValueError):
            return m.group(0)  # keep original on failure

    return _M5_PATTERN.sub(replacer, text)


def decode_all_texts(
    texts: "list[TextEntity]",  # type: ignore[name-defined]  # noqa: F821
) -> "list[TextEntity]":  # noqa: F821
    """Apply decode_m5 to all TextEntity.text fields.

    Args:
        texts: List of TextEntity objects with raw text.

    Returns:
        New list of TextEntity objects with decoded text.
    """
    # Avoid circular import by importing lazily
    from .models import TextEntity

    return [
        TextEntity(
            x=t.x,
            y=t.y,
            text=decode_m5(t.text),
            height=t.height,
            layer=t.layer,
        )
        for t in texts
    ]
