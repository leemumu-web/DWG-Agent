from pathlib import Path

from steel_dxf_split.bh_ir import BHDocumentIR
from steel_dxf_split.bh_passes import (
    BHCompileContext,
    FrontendPass,
    NormalizeFramePass,
)
from steel_dxf_split.bh_source import SourceDocument
from steel_dxf_split.dxf_io import load_document


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "samples" / "bh_pairs" / "2b1-cb-26_拆板前.dxf"


def _context() -> BHCompileContext:
    return BHCompileContext(doc=load_document(SOURCE), source_path=SOURCE)


def test_frontend_decodes_source_ir_without_materializing_lowering_ir() -> None:
    context = _context()

    FrontendPass().run(context)

    assert isinstance(context.source_ir, SourceDocument)
    assert context.lowering_ir is None


def test_frame_stage_materializes_a_distinct_lowering_ir() -> None:
    context = _context()
    FrontendPass().run(context)

    NormalizeFramePass().run(context)

    assert isinstance(context.source_ir, SourceDocument)
    assert isinstance(context.lowering_ir, BHDocumentIR)
    assert not hasattr(context.lowering_ir, "source_document")


def test_compiler_trace_types_are_not_part_of_geometry_lowering_ir() -> None:
    from steel_dxf_split import bh_ir, bh_trace

    assert not hasattr(bh_ir, "BHCompilerTrace")
    assert not hasattr(bh_ir, "StageRecord")
    assert hasattr(bh_trace, "BHCompilerTrace")
    assert hasattr(bh_trace, "StageRecord")
