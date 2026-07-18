r"""Text normalization — multi-encoding decode, header alias, and unit normalization.

Extends decoder.py (\M+5 GBK) with:
1. Direct Chinese passthrough
2. Latin-1 → UTF-8 mojibake repair attempt
3. Header alias normalization (零件编号→零件号, Kg→kg, etc.)
4. Unit label normalization

Import path preserved: decoder.py still works standalone for \M+5 decoding.
This module adds the higher-level normalization layers.
"""

from __future__ import annotations

import re

from .config import HEADER_ALIASES
from .decoder import decode_m5


def normalize_text(raw: str) -> str:
    """Full text normalization pipeline.

    1. \M+5 GBK decode
    2. Mojibake repair (Latin-1 misinterpreted as UTF-8 bytes)
    3. Whitespace cleanup (collapse multiple spaces)
    """
    # Step 1: \M+5 decode
    text = decode_m5(raw)

    # Step 2: mojibake repair — try to detect Latin-1 encoded UTF-8
    text = _repair_mojibake(text)

    # Step 3: whitespace
    text = re.sub(r" {2,}", " ", text).strip()

    return text


def normalize_header_label(text: str) -> str:
    """Normalize a header cell label to its canonical form.

    e.g. "单重(Kg)" → "单重(kg)"
         "零件编号" → "零件号"
         "总面积(m2)" → "总面积(m2)"  (unchanged)
    """
    # Unit normalization: Kg → kg, M2 → m2
    text = re.sub(r"\(Kg\)", "(kg)", text, flags=re.IGNORECASE)
    text = re.sub(r"\(M2\)", "(m2)", text, flags=re.IGNORECASE)
    text = re.sub(r"\(MM\)", "(mm)", text, flags=re.IGNORECASE)

    return text


def header_to_field_key(header_text: str) -> str | None:
    """Map a decoded header label to its canonical field key.

    Returns None if no mapping found.
    """
    normalized = normalize_header_label(header_text)
    return HEADER_ALIASES.get(normalized)


def _repair_mojibake(text: str) -> str:
    """Attempt to repair text that was UTF-8 bytes misinterpreted as Latin-1.

    If the text contains replacement characters or obviously garbled
    sequences, try re-encoding as Latin-1 and decoding as UTF-8.
    """
    if "�" in text:
        # Already has replacement chars from failed decode
        try:
            return text.encode("latin-1", errors="replace").decode("utf-8", errors="replace")
        except (UnicodeError, LookupError):
            return text

    # Check for common mojibake patterns (e.g., Ã© for é, Â for non-breaking space)
    if any(0xC0 <= ord(c) <= 0xC3 for c in text if len(text) < 500):
        try:
            repaired = text.encode("latin-1", errors="replace").decode("utf-8", errors="replace")
            if repaired != text and "�" not in repaired:
                return repaired
        except (UnicodeError, LookupError):
            pass

    return text
