"""Normalize generated Excel business headers without changing their meaning."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_HEADER_BASES = frozenset(
    {
        "序号", "批次", "构件编号", "构件号", "零件号", "零件编号",
        "导入构件编号", "导入零件号", "规格", "截面型材", "截面规格", "型材",
        "长度", "零件长度", "宽度", "高度", "材质", "数量", "原数量",
        "构件数", "总数", "总长", "下料长度", "左进", "右进", "比重",
        "比重来源", "理单重", "理总重", "净材利用率", "重量核验",
        "单净重", "总净重", "表净重", "单毛重", "总毛重", "表毛重",
        "单表面积", "总表面积", "备注", "文件", "类型",
        "级别", "类别", "来源位置", "涉及字段", "说明", "建议操作",
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
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = "".join(text.split())
    if not text:
        return ""
    # Preserve the old importer contract for arbitrary parenthetical
    # annotations, including report-only columns not in the base-name list.
    text = re.split(r"\(", text, maxsplit=1)[0]
    for base in sorted(_HEADER_BASES, key=len, reverse=True):
        if text == base:
            return base
        if text.startswith(base):
            suffix = text[len(base):]
            if _BRACKET_SUFFIX.fullmatch(suffix) or _UNIT_SUFFIX.fullmatch(suffix):
                return base
    return text
