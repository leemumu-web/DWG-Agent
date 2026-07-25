from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable

from ezdxf.document import Drawing
from ezdxf.math import Matrix44
import pytest

from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.dxf_io import load_document

from bh_transform_fixtures import (
    explode_top_level_inserts,
    assembly_signature,
    transform_modelspace,
    uppercase_semantic_layers,
)


ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "samples" / "bh_pairs"


def _translate(doc: Drawing) -> Drawing:
    return transform_modelspace(doc, Matrix44.translate(1234.5, -678.25, 0.0))


def _mirror_x(doc: Drawing) -> Drawing:
    return transform_modelspace(doc, Matrix44.scale(-1.0, 1.0, 1.0))


def _mirror_y(doc: Drawing) -> Drawing:
    return transform_modelspace(doc, Matrix44.scale(1.0, -1.0, 1.0))


def _explode_then_translate(doc: Drawing) -> Drawing:
    return _translate(explode_top_level_inserts(doc))


def _explode_then_mirror_x(doc: Drawing) -> Drawing:
    return _mirror_x(explode_top_level_inserts(doc))


def _explode_then_mirror_y(doc: Drawing) -> Drawing:
    return _mirror_y(explode_top_level_inserts(doc))


STRICT_MUTATIONS: tuple[tuple[str, Callable[[Drawing], Drawing]], ...] = (
    ("translate", _translate),
    ("mirror_x", _mirror_x),
    ("mirror_y", _mirror_y),
    ("uppercase_layers", uppercase_semantic_layers),
    ("explode", explode_top_level_inserts),
    ("explode_translate", _explode_then_translate),
    ("explode_mirror_x", _explode_then_mirror_x),
    ("explode_mirror_y", _explode_then_mirror_y),
)

STEMS = tuple(
    path.name.removesuffix("_拆板前.dxf")
    for path in sorted(PAIR_DIR.glob("*_拆板前.dxf"))
)


@lru_cache(maxsize=None)
def _expected_signature(stem: str) -> dict[str, object]:
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    compiled = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    return {
        "writer_assembly": assembly_signature(compiled.assembly),
        "manufacturing_ir": compiled.manufacturing_ir.fingerprint,
    }


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize(
    ("mutation_name", "mutation"),
    STRICT_MUTATIONS,
    ids=[item[0] for item in STRICT_MUTATIONS],
)
def test_equivalent_drawing_representation_preserves_manufacturing_semantics(
    stem: str,
    mutation_name: str,
    mutation: Callable[[Drawing], Drawing],
) -> None:
    source = PAIR_DIR / f"{stem}_拆板前.dxf"
    expected = _expected_signature(stem)
    mutated = mutation(load_document(source))
    compiled = compile_bh_document(
        mutated,
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    actual = {
        "writer_assembly": assembly_signature(compiled.assembly),
        "manufacturing_ir": compiled.manufacturing_ir.fingerprint,
    }

    assert actual == expected, f"semantic drift after {mutation_name}"
