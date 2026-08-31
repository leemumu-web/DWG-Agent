from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import ezdxf
from ezdxf.entities import DXFEntity, Insert
from ezdxf.lldxf.encoding import decode_mif_to_unicode


class DXFLoadError(ValueError):
    """A source drawing could not be recovered into an auditable DXF."""


def load_document(path: str | Path) -> ezdxf.document.Drawing:
    source = Path(path)
    if source.suffix.lower() == ".dwg":
        raise DXFLoadError(
            "DWG is not directly readable by ezdxf; convert it to DXF first."
        )
    try:
        document = ezdxf.readfile(source)
    except ezdxf.DXFStructureError:
        from ezdxf import recover

        document, auditor = recover.readfile(source)
        if auditor.has_errors:
            raise DXFLoadError(
                f"Recovered DXF still has {len(auditor.errors)} audit errors."
            )
    auditor = document.audit()
    if auditor.has_errors:
        raise DXFLoadError(f"Input DXF has {len(auditor.errors)} audit errors.")
    return document


def decode_cad_text_transport(value: str) -> str:
    """Decode CAD transport escapes without changing text layout or meaning.

    This boundary is shared by semantic parsing and human previews.  It keeps
    MTEXT formatting such as ``\\P``/``\\H`` intact while decoding characters
    carried by DXF Unicode, MIF, percent escapes, or the validated legacy
    cp936 diameter-symbol dialect.
    """

    # MIF escapes are part of the DXF text encoding layer.  Decode them before
    # interpreting digits: ``\\M+5A6B5`` is the cp936 code for ``Φ``, not the
    # literal digit 5 followed by a diameter value.
    value = decode_mif_to_unicode(value)
    value = ezdxf.decode_dxf_unicode(value)
    value = value.replace("%%c", "Φ").replace("%%C", "Φ")
    # Older Tekla/DWG-to-DXF paths can decode the same cp936 bytes A6 B5 as
    # two single-byte glyphs.  Canonicalize that known diameter-symbol dialect
    # while the value is still only transport text.
    return value.replace("¦µ", "Φ").replace("¦μ", "Φ")


def normalize_text(value: str) -> str:
    """Normalize decoded DXF text without assigning engineering meaning."""

    value = decode_cad_text_transport(value)
    value = value.replace("\\P", " ")
    value = re.sub(r"\\[A-Za-z][^;]*;", "", value)
    value = value.replace("{", "").replace("}", "")
    value = unicodedata.normalize("NFKC", value)
    return " ".join(value.strip().split())


def recursive_virtual_entities(insert: Insert) -> Iterator[DXFEntity]:
    """Yield non-INSERT entities with every nested instance transform applied."""

    for entity in insert.virtual_entities():
        if entity.dxftype() == "INSERT":
            yield from recursive_virtual_entities(entity)
        else:
            yield entity


def iter_modelspace_entities(
    document: ezdxf.document.Drawing,
) -> Iterator[DXFEntity]:
    """Yield direct and recursively expanded model-space source entities."""

    for entity in document.modelspace():
        if entity.dxftype() == "INSERT":
            yield from recursive_virtual_entities(entity)
        else:
            yield entity
