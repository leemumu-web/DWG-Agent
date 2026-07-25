from pathlib import Path

import ezdxf
import pytest

from steel_dxf_split import bh_pipeline, bh_solver
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_passes import (
    AnnotationPass,
    BHCompileContext,
    FrontendPass,
    MetadataPass,
    NormalizeFramePass,
    HypothesisSolvePass,
)
from steel_dxf_split.dxf_io import load_document


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "samples" / "bh_pairs" / "2b1-cb-26_拆板前.dxf"


def _context_ready_for_solving() -> BHCompileContext:
    context = BHCompileContext(doc=load_document(SOURCE), source_path=SOURCE)
    for compiler_pass in (
        FrontendPass(),
        NormalizeFramePass(),
        AnnotationPass(),
        MetadataPass(),
    ):
        compiler_pass.run(context)
    return context


def test_solver_propagates_an_unexpected_programming_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context_ready_for_solving()

    def broken_lowering(*args, **kwargs):
        del args, kwargs
        raise TypeError("programming defect")

    monkeypatch.setattr(bh_solver, "lower_bh_assembly", broken_lowering)

    with pytest.raises(TypeError, match="programming defect"):
        HypothesisSolvePass().run(context)


def test_pipeline_does_not_publish_a_domain_rejection_for_plain_value_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_compiler(*args, **kwargs):
        del args, kwargs
        raise ValueError("unclassified programming defect")

    monkeypatch.setattr(bh_pipeline, "compile_bh_document", broken_compiler)

    with pytest.raises(ValueError, match="unclassified programming defect"):
        bh_pipeline.split_bh_dxf(
            SOURCE,
            tmp_path / "output",
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        )

    assert not (tmp_path / "output").exists()


def test_expected_frame_failure_is_an_auditable_domain_rejection(
    tmp_path: Path,
) -> None:
    document = ezdxf.new("R2007")
    document.header["$INSUNITS"] = 4
    document.modelspace().add_text(
        "BH300*200*8*12",
        dxfattribs={"insert": (0.0, 0.0), "height": 10.0},
    )
    source = tmp_path / "no-physical-part-edges.dxf"
    document.saveas(source)

    clean, review, report_path, report = bh_pipeline.split_bh_dxf(
        source,
        tmp_path / "output",
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
    )

    assert clean is None
    assert review is None
    assert report["automation_route"] == "rejected"
    assert report["diagnostic_codes"] == ["BH-FRAME-INFERENCE-FAILED"]
    assert report["compilation_error"]["type"] == "FrameInferenceError"
    assert report_path.exists()
