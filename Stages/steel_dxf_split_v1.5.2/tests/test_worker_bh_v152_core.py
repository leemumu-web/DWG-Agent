from __future__ import annotations

import inspect

import steel_dxf_split
import steel_dxf_split.pipeline as pipeline


def test_root_worker_uses_bh_v152_as_its_native_core() -> None:
    assert steel_dxf_split.__version__ == "1.5.2"
    from steel_dxf_split import bh_bolt_semantics

    assert callable(bh_bolt_semantics.opening_nominal_width)


def test_worker_pipeline_is_a_thin_two_family_dispatch() -> None:
    source = inspect.getsource(pipeline)
    assert "from .bh_pipeline import split_bh_dxf" in source
    assert "from .box.compiler import" in source
    assert "steel_dxf_split.application" not in source
    assert "Adapter" not in source
    assert "fallback" not in source.casefold()
