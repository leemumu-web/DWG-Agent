from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.entities import DXFEntity, Insert

from .model import TextFact
from .text import normalize_text


_CHINESE_CODEPAGES = {
    "GB2312": "gbk",
    "ANSI_936": "gbk",
}


class DXFReadError(RuntimeError):
    pass


def _read_document(path: Path) -> tuple[ezdxf.document.Drawing, str, str]:
    try:
        detected = ezdxf.readfile(path)
        source_codepage = str(detected.header.get("$DWGCODEPAGE", "")).upper()
        override = _CHINESE_CODEPAGES.get(source_codepage)
        if override is None:
            return detected, source_codepage, detected.encoding
        return ezdxf.readfile(path, encoding=override), source_codepage, override
    except Exception as exc:
        raise DXFReadError(f"unable to read DXF {path.name}: {exc}") from exc


def _entity_text(entity: DXFEntity) -> str | None:
    entity_type = entity.dxftype()
    if entity_type in {"TEXT", "ATTRIB"}:
        return str(entity.dxf.text)
    if entity_type == "MTEXT":
        return str(entity.plain_text())
    return None


def _entity_height(entity: DXFEntity) -> float:
    if entity.dxftype() == "MTEXT":
        return float(entity.dxf.get("char_height", 1.0) or 1.0)
    return float(entity.dxf.get("height", 1.0) or 1.0)


def _fact(entity: DXFEntity, block_path: tuple[str, ...]) -> TextFact | None:
    raw = _entity_text(entity)
    if raw is None:
        return None
    normalized = normalize_text(raw)
    if not normalized:
        return None
    insert = entity.dxf.get("insert")
    if insert is None:
        return None
    return TextFact(
        raw=raw,
        normalized=normalized,
        x=float(insert.x),
        y=float(insert.y),
        height=_entity_height(entity),
        entity_type=entity.dxftype(),
        layer=str(entity.dxf.get("layer", "")),
        handle=str(entity.dxf.handle) if entity.dxf.handle is not None else None,
        block_path=block_path,
    )


def _walk_insert(insert: Insert, parent_path: tuple[str, ...]) -> Iterator[TextFact]:
    block_path = (*parent_path, str(insert.dxf.name))
    for attribute in insert.attribs:
        fact = _fact(attribute, block_path)
        if fact is not None:
            yield fact
    for entity in insert.virtual_entities():
        if entity.dxftype() == "INSERT":
            yield from _walk_insert(entity, block_path)  # type: ignore[arg-type]
            continue
        fact = _fact(entity, block_path)
        if fact is not None:
            yield fact


def read_text_facts(path: str | Path) -> tuple[list[TextFact], dict[str, Any]]:
    source = Path(path)
    document, source_codepage, decoder = _read_document(source)
    facts: list[TextFact] = []
    try:
        for entity in document.modelspace():
            if entity.dxftype() == "INSERT":
                facts.extend(_walk_insert(entity, ()))  # type: ignore[arg-type]
                continue
            fact = _fact(entity, ())
            if fact is not None:
                facts.append(fact)
    except Exception as exc:
        raise DXFReadError(f"unable to traverse DXF {source.name}: {exc}") from exc
    facts.sort(key=lambda item: (-item.y, item.x, item.normalized, item.handle or ""))
    return facts, {
        "source_codepage": source_codepage,
        "preview_encoding": decoder,
        "text_fact_count": len(facts),
    }
