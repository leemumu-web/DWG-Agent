from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import ezdxf
from ezdxf.entities.dxfentity import DXFEntity
from ezdxf.entities.insert import Insert
from ezdxf.filemanagement import readfile
from ezdxf.lldxf.const import DXFStructureError
from ezdxf.lldxf.encoding import decode_dxf_unicode, decode_mif_to_unicode


class DXFLoadError(ValueError):
    """A source drawing could not be recovered into an auditable DXF."""


def load_document(path: str | Path) -> ezdxf.document.Drawing:
    """Read and audit a DXF, recovering only when ezdxf can prove validity."""

    source = Path(path)
    if source.suffix.lower() == ".dwg":
        raise DXFLoadError(
            "DWG is not directly readable by ezdxf; convert it to DXF first."
        )
    try:
        document = readfile(source)
    except DXFStructureError as error:
        from ezdxf import recover

        document, auditor = recover.readfile(source)
        if auditor.has_errors:
            raise DXFLoadError(
                f"Recovered DXF still has {len(auditor.errors)} audit errors."
            ) from error
    auditor = document.audit()
    if auditor.has_errors:
        raise DXFLoadError(f"Input DXF has {len(auditor.errors)} audit errors.")
    return document


def decode_cad_text_transport(value: str) -> str:
    """Decode CAD transport escapes without assigning engineering meaning.

    This boundary is shared by semantic parsing and the non-authoritative
    preview renderer.  MTEXT layout controls such as ``\\P`` remain intact.
    """

    value = decode_mif_to_unicode(value)
    value = decode_dxf_unicode(value)
    value = value.replace("%%c", "Φ").replace("%%C", "Φ")
    return value.replace("¦µ", "Φ").replace("¦μ", "Φ")


def normalize_text(value: str) -> str:
    """Normalize already transported CAD text for metadata interpretation."""

    value = decode_cad_text_transport(value)
    value = value.replace("\\P", " ")
    value = re.sub(r"\\[A-Za-z][^;]*;", "", value)
    value = value.replace("{", "").replace("}", "")
    value = unicodedata.normalize("NFKC", value)
    return " ".join(value.strip().split())


def recursive_virtual_entities(insert: Insert) -> Iterator[DXFEntity]:
    """Yield non-INSERT entities with every nested transform applied."""

    for entity in insert.virtual_entities():
        if entity.dxftype() == "INSERT":
            assert isinstance(entity, Insert)
            yield from recursive_virtual_entities(entity)
        else:
            yield entity


def iter_modelspace_entities(
    document: ezdxf.document.Drawing,
) -> Iterator[DXFEntity]:
    """Yield direct and recursively expanded model-space entities."""

    for entity in document.modelspace():
        if entity.dxftype() == "INSERT":
            assert isinstance(entity, Insert)
            yield from recursive_virtual_entities(entity)
        else:
            yield entity
