from __future__ import annotations

from io import StringIO

import ezdxf
from ezdxf.document import Drawing
from ezdxf.math import Matrix44

from steel_dxf_split.bh_fingerprint import manufacturing_payload


def clone_document(doc: Drawing) -> Drawing:
    """Round-trip a document without sharing ezdxf entity state."""

    stream = StringIO()
    doc.write(stream)
    stream.seek(0)
    return ezdxf.read(stream)


def transform_modelspace(doc: Drawing, matrix: Matrix44) -> Drawing:
    """Apply one drawing-space transform to every top-level entity."""

    mutated = clone_document(doc)
    for entity in list(mutated.modelspace()):
        entity.transform(matrix)
    return mutated


def uppercase_semantic_layers(doc: Drawing) -> Drawing:
    """Change only layer spelling, including entities stored in blocks."""

    mutated = clone_document(doc)
    for entity in mutated.entitydb.values():
        if entity.is_alive and entity.dxf.is_supported("layer"):
            entity.dxf.layer = str(entity.dxf.layer).upper()
    return mutated


def explode_top_level_inserts(doc: Drawing) -> Drawing:
    """Replace modelspace INSERTs with equivalent transformed entities."""

    mutated = clone_document(doc)
    modelspace = mutated.modelspace()
    for insert in list(modelspace.query("INSERT")):
        insert.explode(target_layout=modelspace)
    return mutated


def assembly_signature(assembly) -> dict[str, object]:
    """Return the canonical manufacturing payload for comparison."""

    return manufacturing_payload(assembly)
