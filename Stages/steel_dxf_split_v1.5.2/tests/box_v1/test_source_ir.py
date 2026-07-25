from __future__ import annotations

import re
from dataclasses import FrozenInstanceError
import pytest

from steel_dxf_split.box.source_ir import (
    SourceEntityIR,
    build_source_ir,
    geometry_fingerprint,
    is_hidden_projection_linetype,
)
from tests.box_v1.paths import INPUTS

SAMPLE = INPUTS / "2b1-cb-56_拆板前.dxf"


def test_hidden_projection_dialect_normalizes_old_and_new_tekla_linetypes() -> None:
    assert is_hidden_projection_linetype("XKITLINE04")
    assert is_hidden_projection_linetype("dot2")
    assert not is_hidden_projection_linetype("XKITLINE00")


def test_source_ir_preserves_tekla_groups_and_source_identity() -> None:
    source = build_source_ir(SAMPLE)

    assert source.dxf_version == "AC1032"
    assert source.units == 4
    assert source.declared_codepage == "GB2312"
    assert re.fullmatch(r"[0-9a-f]{64}", source.file_sha256)
    assert re.fullmatch(r"[0-9a-f]{64}", source.geometry_fingerprint)
    assert len(source.groups_by_layer("Part")) == 2
    assert all(group.block_name.startswith("*A") for group in source.groups)

    part_entities = source.entities_by_layer("Part")
    assert len(part_entities) == 18
    assert {entity.kind for entity in part_entities} == {"LINE"}
    assert {entity.linetype for entity in part_entities} == {
        "XKITLINE00",
        "XKITLINE04",
    }
    assert len({entity.source_id for entity in source.entities}) == len(source.entities)


def test_source_ir_is_immutable() -> None:
    source = build_source_ir(SAMPLE)

    with pytest.raises(FrozenInstanceError):
        source.units = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        source.entities[0].layer = "changed"  # type: ignore[misc]


def test_geometry_fingerprint_is_entity_enumeration_invariant() -> None:
    source = build_source_ir(SAMPLE)

    assert geometry_fingerprint(source.entities) == geometry_fingerprint(
        tuple(reversed(source.entities))
    )


def test_geometry_fingerprint_changes_when_source_geometry_changes() -> None:
    source = build_source_ir(SAMPLE)
    original = source.entities_by_layer("Part")[0]
    changed = SourceEntityIR(
        source_id=original.source_id,
        group_id=original.group_id,
        handle=original.handle,
        kind=original.kind,
        layer=original.layer,
        linetype=original.linetype,
        start=(original.start[0] + 1.0, original.start[1]),
        end=original.end,
    )

    assert geometry_fingerprint((original,)) != geometry_fingerprint((changed,))


def test_all_twenty_inputs_have_two_part_object_groups() -> None:
    paths = sorted(INPUTS.glob("*_拆板前.dxf"))

    assert len(paths) == 20
    for path in paths:
        source = build_source_ir(path)
        assert len(source.groups_by_layer("Part")) == 2, path.name
