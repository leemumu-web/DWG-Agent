"""Deterministic normalization for Excel business headers.

Header units are presentation metadata.  They must not change the canonical
business field selected by the input contract.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


_HEADER_BASES = frozenset(
    {
        "批次",
        "构件编号",
        "构件号",
        "零件号",
        "零件编号",
        "规格",
        "型材",
        "截面型材",
        "截面规格",
        "零件长度",
        "长度",
        "构件长度",
        "宽度",
        "构件宽度",
        "高度",
        "构件高度",
        "材质",
        "数量",
        "构件数",
        "单净重",
        "总净重",
        "单毛重",
        "总毛重",
        "单重",
        "总重",
        "单表面积",
        "总表面积",
        "单面积",
        "总面积",
        "单涂装面积",
        "总涂装面积",
        "版本",
        "备注",
        "序号",
        "下料长度",
        "左进",
        "右进",
        "比重",
        "理单重",
        "理总重",
        "净材利用率",
        "重量核验",
        "级别",
        "类别",
        "说明",
        "建议操作",
    }
)

_UNIT = (
    r"(?:mm|cm|m|kg|g|t|m2|m²|㎡|毫米|厘米|米|千克|公斤|克|吨|"
    r"平方米|平方毫米|件|个|pcs?|片|根|套|台|条|%|‰)"
)
_BRACKET_SUFFIX = re.compile(
    r"(?:\([^()]*\)|\[[^\[\]]*\]|【[^【】]*】)$",
    re.IGNORECASE,
)
_UNIT_SUFFIX = re.compile(
    rf"(?:[/\\:：·_\-]?{_UNIT})+$",
    re.IGNORECASE,
)


def normalize_header(value: Any) -> str:
    """Return a canonical-looking header while preserving business text.

    Only known header names can be shortened.  A suffix is removed when it is
    a bracketed annotation (legacy behavior) or a recognized unit, optionally
    separated by ``/``, ``-`` or whitespace.  Arbitrary trailing text remains
    untouched and is therefore still rejected by the alias map.
    """

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = "".join(text.split())
    if not text:
        return ""
    # Keep compatibility with the historical parser, which ignored any
    # parenthetical annotation after a canonical header.
    text = re.split(r"\(", text, maxsplit=1)[0]
    for base in sorted(_HEADER_BASES, key=len, reverse=True):
        if text == base:
            return base
        if not text.startswith(base):
            continue
        suffix = text[len(base):]
        if _BRACKET_SUFFIX.fullmatch(suffix) or _UNIT_SUFFIX.fullmatch(suffix):
            return base
    return text
