from __future__ import annotations

import ezdxf
import pytest

from steel_dxf_split.profile_detection import (
    ProfileFamilyConflictError,
    detect_profile_family,
)


def _drawing(*values: str) -> ezdxf.document.Drawing:
    document = ezdxf.new()
    modelspace = document.modelspace()
    for index, value in enumerate(values):
        modelspace.add_text(
            value,
            dxfattribs={"insert": (float(index), 0.0)},
        )
    return document


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("BOX600*500*20*25",), "BOX"),
        (("BH600*300*12*20",), "BH"),
        (("BOX600*500*20*25", "BOX600*500*20*25"), "BOX"),
        ((), None),
    ],
)
def test_profile_family_requires_one_unique_semantic_family(
    values: tuple[str, ...],
    expected: str | None,
) -> None:
    assert detect_profile_family(_drawing(*values)) == expected


@pytest.mark.parametrize(
    "values",
    [
        ("BOX600*500*20*25", "BH600*300*12*20"),
        ("BH600*300*12*20", "BOX600*500*20*25"),
    ],
)
def test_mixed_profile_families_fail_independent_of_entity_order(
    values: tuple[str, str],
) -> None:
    with pytest.raises(
        ProfileFamilyConflictError,
        match="BOX.*BH|BH.*BOX",
    ):
        detect_profile_family(_drawing(*values))
