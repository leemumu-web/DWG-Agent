from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf
from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split.box.compiler import BoxCompileConfig, compile_box
from steel_dxf_split.box.contracts import BoxSourceContract, BoxSourceLimits
from steel_dxf_split.box.frontend import BoxSourceLimitError, run_frontend


def _save(document, path: Path) -> Path:
    document.saveas(path)
    return path


def _assert_limit(path: Path, limits: BoxSourceLimits, code: str) -> None:
    with pytest.raises(BoxSourceLimitError) as captured:
        run_frontend(path, limits=limits)
    assert captured.value.reason_code == code


def test_compressed_source_limits_are_frozen_and_serializable() -> None:
    limits = BoxSourceLimits()

    assert limits.to_dict() == {
        "max_entities": 200_000,
        "max_text_entities": 50_000,
        "max_points_per_entity": 20_000,
        "max_block_depth": 16,
        "max_abs_coordinate": 1.0e9,
    }
    with pytest.raises(FrozenInstanceError):
        limits.max_entities = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("max_entities", 0),
        ("max_text_entities", 0),
        ("max_points_per_entity", 0),
        ("max_block_depth", 0),
        ("max_abs_coordinate", 0.0),
        ("max_abs_coordinate", inf),
    ),
)
def test_compressed_source_limits_reject_invalid_values(
    name: str,
    value: int | float,
) -> None:
    with pytest.raises(ValueError, match=name):
        BoxSourceLimits(**{name: value})  # type: ignore[arg-type]


def test_frontend_rejects_entity_budget(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_line((0, 0), (1, 0))
    document.modelspace().add_line((1, 0), (1, 1))

    _assert_limit(
        _save(document, tmp_path / "entities.dxf"),
        BoxSourceLimits(max_entities=1),
        "source_entity_limit_exceeded",
    )


def test_frontend_rejects_text_budget(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_text("A")
    document.modelspace().add_text("B")

    _assert_limit(
        _save(document, tmp_path / "texts.dxf"),
        BoxSourceLimits(max_text_entities=1),
        "source_text_limit_exceeded",
    )


def test_frontend_rejects_points_budget(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_lwpolyline(((0, 0), (1, 0), (1, 1)))

    _assert_limit(
        _save(document, tmp_path / "points.dxf"),
        BoxSourceLimits(max_points_per_entity=2),
        "source_points_limit_exceeded",
    )


def test_frontend_rejects_block_depth(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    inner = document.blocks.new("INNER")
    inner.add_line((0, 0), (1, 0))
    outer = document.blocks.new("OUTER")
    outer.add_blockref("INNER", (0, 0))
    document.modelspace().add_blockref("OUTER", (0, 0))

    _assert_limit(
        _save(document, tmp_path / "depth.dxf"),
        BoxSourceLimits(max_block_depth=1),
        "source_block_depth_limit_exceeded",
    )


def test_frontend_rejects_coordinate_budget(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_line((0, 0), (11, 0))

    _assert_limit(
        _save(document, tmp_path / "coordinate.dxf"),
        BoxSourceLimits(max_abs_coordinate=10.0),
        "source_coordinate_limit_exceeded",
    )


def test_compile_box_propagates_limits_to_the_only_frontend(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_line((0, 0), (1, 0))
    document.modelspace().add_line((1, 0), (1, 1))
    source = _save(document, tmp_path / "compiler-limit.dxf")
    config = BoxCompileConfig(
        output_dir=tmp_path / "output",
        source_contract=BoxSourceContract(),
        source_limits=BoxSourceLimits(max_entities=1),
    )

    with pytest.raises(BoxSourceLimitError) as captured:
        compile_box(source, config=config)

    assert captured.value.reason_code == "source_entity_limit_exceeded"
    assert not (tmp_path / "output").exists()
