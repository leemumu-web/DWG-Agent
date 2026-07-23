from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import ezdxf
from ezdxf.entities import DXFEntity, Insert

from .models import Evidence, ParseError
from .text import normalize_text

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
