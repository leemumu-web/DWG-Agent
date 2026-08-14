"""从 DXF 提取解析证据（Evidence）的读取器。

遍历模型空间与 INSERT 块引用（含嵌套展开），把文字/线/块按证据字段收集；
``_walk_insert`` 中单个实体损坏不中断整体遍历，只通过 ``anomalies``
标志记录 STRUCTURE_ANOMALY。证据记录世界坐标而非块内局部坐标。
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

import ezdxf
from ezdxf.entities import DXFEntity, Insert

from .models import Evidence, ParseError, ParseWarning, StandardOffcut
from .text import normalize_text


_STANDARD_OFFCUT_BLOCK_NAME = "offcut_zh_cn"
_REQUIRED_STANDARD_OFFCUT_ATTRIBUTES = ("GG", "CZ", "YLBH")
_DIMENSION = r"([+-]?\s*(?:\d+(?:\.\d*)?|\.\d+))"
_STANDARD_OFFCUT_SPECIFICATION = re.compile(
    rf"^\s*{_DIMENSION}\s*[xX×]\s*{_DIMENSION}\s*[xX×]\s*{_DIMENSION}\s*$"
)

def _read_document(path: Path):
    try:
        # ezdxf already applies the DXF-version encoding rules.  In particular,
        # R2007+ files store strings as UTF-8 even when a converter preserves an
        # old ANSI_936 header.  Forcing GBK here corrupts otherwise valid Chinese.
        return ezdxf.readfile(path)
    except Exception as exc:
        raise ParseError("REMNANT_DXF_UNREADABLE") from exc


def _entity_text(entity: DXFEntity) -> str | None:
    if entity.dxftype() in {"TEXT", "ATTRIB"}:
        return str(entity.dxf.text)
    if entity.dxftype() == "MTEXT":
        return str(entity.plain_text())
    return None


def _evidence(entity: DXFEntity, block_path: tuple[str, ...]) -> Evidence | None:
    raw = _entity_text(entity)
    if raw is None:
        return None
    insert = entity.dxf.get("insert")
    if insert is None:
        return None
    normalized = normalize_text(raw)
    if not normalized:
        return None
    source = getattr(entity, "source_of_copy", None)
    handle = entity.dxf.handle or (source.dxf.handle if source is not None else None)
    return Evidence(raw, normalized, entity.dxftype(), str(entity.dxf.get("layer", "")),
                    list(block_path), float(insert.x), float(insert.y),
                    str(handle) if handle is not None else None)


def _walk_insert(
    insert: Insert, parents: tuple[str, ...], anomalies: list[bool]
) -> Iterator[Evidence]:
    path = (*parents, str(insert.dxf.name))
    for attribute in insert.attribs:
        try:
            item = _evidence(attribute, path)
        except Exception:
            anomalies[0] = True
            continue
        if item:
            yield item
    try:
        for entity in insert.virtual_entities():
            try:
                if entity.dxftype() == "INSERT":
                    yield from _walk_insert(entity, path, anomalies)  # type: ignore[arg-type]
                else:
                    item = _evidence(entity, path)
                    if item:
                        yield item
            except Exception:
                anomalies[0] = True
    except Exception:
        anomalies[0] = True


def read_evidence(path: Path) -> tuple[list[Evidence], bool]:
    document = _read_document(path)
    found: list[Evidence] = []
    anomalies = [False]
    try:
        for entity in document.modelspace():
            try:
                if entity.dxftype() == "INSERT":
                    found.extend(_walk_insert(entity, (), anomalies))  # type: ignore[arg-type]
                else:
                    item = _evidence(entity, ())
                    if item:
                        found.append(item)
            except Exception:
                anomalies[0] = True
    except Exception as exc:
        if not found:
            raise ParseError("REMNANT_DXF_UNREADABLE") from exc
        anomalies[0] = True
    return found, anomalies[0]


def _standard_offcut_inserts(document) -> Iterator[Insert]:
    def walk(insert: Insert) -> Iterator[Insert]:
        if str(insert.dxf.name).casefold() == _STANDARD_OFFCUT_BLOCK_NAME:
            yield insert
        try:
            for entity in insert.virtual_entities():
                if entity.dxftype() == "INSERT":
                    yield from walk(entity)  # type: ignore[arg-type]
        except Exception:
            return

    for entity in document.modelspace():
        if entity.dxftype() == "INSERT":
            yield from walk(entity)  # type: ignore[arg-type]


def _parse_standard_offcut_specification(
    value: str,
) -> tuple[Decimal, Decimal, Decimal] | None:
    match = _STANDARD_OFFCUT_SPECIFICATION.fullmatch(normalize_text(value))
    if match is None:
        return None
    try:
        thickness, length, width = (
            Decimal(part.replace(" ", "")) for part in match.groups()
        )
    except InvalidOperation:
        return None
    thickness = abs(thickness)
    if thickness <= 0 or length <= 0 or width <= 0:
        return None
    return thickness, length, width


def read_standard_offcut(path: Path) -> tuple[StandardOffcut | None, list[ParseWarning]]:
    document = _read_document(path)
    inserts = list(_standard_offcut_inserts(document))
    if not inserts:
        return None, [
            ParseWarning(
                "STANDARD_OFFCUT_MISSING", "图纸中未找到标准余料块 offcut_zh_cn"
            )
        ]
    if len(inserts) > 1:
        return None, [
            ParseWarning(
                "STANDARD_OFFCUT_DUPLICATE", "图纸中存在多个标准余料块 offcut_zh_cn"
            )
        ]

    insert = inserts[0]
    attributes = {
        str(attribute.dxf.tag): str(attribute.dxf.text)
        for attribute in insert.attribs
    }
    missing = [
        tag
        for tag in _REQUIRED_STANDARD_OFFCUT_ATTRIBUTES
        if not normalize_text(attributes.get(tag, ""))
    ]
    if missing:
        return None, [
            ParseWarning(
                "STANDARD_OFFCUT_MISSING_REQUIRED_ATTRIBUTE",
                f"标准余料块缺少必要属性：{'、'.join(missing)}",
            )
        ]

    raw_specification = attributes["GG"]
    dimensions = _parse_standard_offcut_specification(raw_specification)
    if dimensions is None:
        return None, [
            ParseWarning(
                "STANDARD_OFFCUT_INVALID_SPECIFICATION", "标准余料块规格 GG 非法"
            )
        ]
    thickness, length, width = dimensions
    return StandardOffcut(
        block_type=str(insert.dxf.name),
        raw_specification=raw_specification,
        thickness=thickness,
        length=length,
        width=width,
        material=normalize_text(attributes["CZ"]),
        remnant_number=normalize_text(attributes["YLBH"]),
    ), []
