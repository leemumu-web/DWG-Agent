from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import ezdxf
from ezdxf.entities import DXFEntity, Insert

from .models import Evidence, ParseError
from .text import normalize_text

_CHINESE_CODEPAGES = {"GB2312": "gbk", "ANSI_936": "gbk"}


def _read_document(path: Path):
    try:
        document = ezdxf.readfile(path)
        encoding = _CHINESE_CODEPAGES.get(str(document.header.get("$DWGCODEPAGE", "")).upper())
        return ezdxf.readfile(path, encoding=encoding) if encoding else document
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
    insert = entity.dxf.get("insert")
    if raw is None or insert is None:
        return None
    normalized = normalize_text(raw)
    if not normalized:
        return None
    source = getattr(entity, "source_of_copy", None)
    handle = entity.dxf.handle or (source.dxf.handle if source is not None else None)
    return Evidence(raw, normalized, entity.dxftype(), str(entity.dxf.get("layer", "")),
                    list(block_path), float(insert.x), float(insert.y),
                    str(handle) if handle is not None else None)


def _walk_insert(insert: Insert, parents: tuple[str, ...]) -> Iterator[Evidence]:
    path = (*parents, str(insert.dxf.name))
    for attribute in insert.attribs:
        item = _evidence(attribute, path)
        if item:
            yield item
    for entity in insert.virtual_entities():
        if entity.dxftype() == "INSERT":
            yield from _walk_insert(entity, path)  # type: ignore[arg-type]
        else:
            item = _evidence(entity, path)
            if item:
                yield item


def read_evidence(path: Path) -> list[Evidence]:
    document = _read_document(path)
    found: list[Evidence] = []
    try:
        for entity in document.modelspace():
            if entity.dxftype() == "INSERT":
                found.extend(_walk_insert(entity, ()))  # type: ignore[arg-type]
            else:
                item = _evidence(entity, ())
                if item:
                    found.append(item)
    except Exception as exc:
        raise ParseError("REMNANT_DXF_UNREADABLE") from exc
    return found
