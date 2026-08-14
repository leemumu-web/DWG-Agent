"""图纸文本规范化管线。

串联四步：\\M+5（ZWCAD BigFont）GBK 解码 → ``ezdxf.decode_dxf_unicode``
（\\U+XXXX 转义）→ NFKC 归一化（全角→半角，使候选分类正则稳定匹配）→
空白折叠。解码失败时保留原文，不抛错。
"""

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
