from __future__ import annotations

import re
import unicodedata

import ezdxf

_WHITESPACE_RE = re.compile(r"\s+")
_GBK_MIF_RE = re.compile(r"\\M\+5([0-9A-Fa-f]{4})")


def _decode_gbk_mif(match: re.Match[str]) -> str:
    try:
        return bytes.fromhex(match.group(1)).decode("gbk")
    except (UnicodeDecodeError, ValueError):
        return match.group(0)


def normalize_text(value: str) -> str:
    normalized = _GBK_MIF_RE.sub(_decode_gbk_mif, value)
    normalized = ezdxf.decode_dxf_unicode(normalized)
    normalized = unicodedata.normalize("NFKC", normalized)
    return _WHITESPACE_RE.sub(" ", normalized).strip()
