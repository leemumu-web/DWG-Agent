from __future__ import annotations

from pathlib import Path

import pytest

from steel_dxf_split.box.metadata import (
    MetadataResolutionError,
    resolve_box_metadata,
)
from steel_dxf_split.box.source_ir import (
    ObjectGroupIR,
    SourceDocumentIR,
    SourceEntityIR,
)


def _title_group(
    group_id: str,
    *,
    insert_handle: str,
    block_name: str,
    member_mark: str = "b4-1-cb-87",
    insert_point: tuple[float, float] = (100.0, 200.0),
) -> tuple[ObjectGroupIR, tuple[SourceEntityIR, ...]]:
    texts = (
        ("profile", "BOX600*600*20*20"),
        ("member", member_mark),
        ("length", "8785"),
        ("material", "Q355B"),
        ("scale", "1:20"),
    )
    entities = tuple(
        SourceEntityIR(
            source_id=f"{group_id}/entity:{name}",
            group_id=group_id,
            handle=f"{insert_handle}:{name}",
            kind="TEXT",
            layer="OtherObjectType",
            linetype="BYLAYER",
            text_raw=value,
            text_decoded=value,
        )
        for name, value in texts
    )
    group = ObjectGroupIR(
        group_id=group_id,
        insert_handle=insert_handle,
        block_name=block_name,
        insert_point=insert_point,
        rotation=0.0,
        scale=(1.0, 1.0, 1.0),
        source_ids=tuple(entity.source_id for entity in entities),
        layers=("OtherObjectType",),
    )
    return group, entities


def _source(
    groups: tuple[ObjectGroupIR, ...],
    entities: tuple[SourceEntityIR, ...],
) -> SourceDocumentIR:
    return SourceDocumentIR(
        path=Path("duplicate-title-groups.dxf"),
        dxf_version="AC1027",
        units=4,
        declared_codepage="ANSI_936",
        detected_encoding="gb18030",
        file_sha256="0" * 64,
        geometry_fingerprint="1" * 64,
        groups=groups,
        entities=entities,
    )


def test_fully_equivalent_duplicate_title_groups_resolve_once() -> None:
    first_group, first_entities = _title_group(
        "insert:A",
        insert_handle="A",
        block_name="CA",
    )
    second_group, second_entities = _title_group(
        "insert:B",
        insert_handle="B",
        block_name="3417",
    )

    metadata = resolve_box_metadata(
        _source(
            (first_group, second_group),
            (*first_entities, *second_entities),
        )
    )

    assert metadata.title_group_id == "insert:A"
    assert metadata.member_mark.value == "b4-1-cb-87"
    assert metadata.profile.value.canonical == "BOX600*600*20*20"
    assert metadata.nominal_length.value == 8785.0


@pytest.mark.parametrize(
    ("member_mark", "insert_point"),
    [
        ("b4-1-cb-88", (100.0, 200.0)),
        ("b4-1-cb-87", (300.0, 200.0)),
    ],
)
def test_non_equivalent_title_groups_still_fail_closed(
    member_mark: str,
    insert_point: tuple[float, float],
) -> None:
    first_group, first_entities = _title_group(
        "insert:A",
        insert_handle="A",
        block_name="CA",
    )
    second_group, second_entities = _title_group(
        "insert:B",
        insert_handle="B",
        block_name="3417",
        member_mark=member_mark,
        insert_point=insert_point,
    )

    with pytest.raises(
        MetadataResolutionError,
        match="equivalent BOX profiles occur in multiple title groups",
    ):
        resolve_box_metadata(
            _source(
                (first_group, second_group),
                (*first_entities, *second_entities),
            )
        )
