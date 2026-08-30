"""The BH dialect must recognize every Tekla hidden-projection linetype spelling.

Tekla exports the same hidden projection role with different DXF linetype
spellings across DWG/DXF generations.  The reference corpus uses ``XKITLINE04``;
a newer export configuration uses ``DASHEDX2`` for the same hidden edges.  If a
spelling is missed, hidden web-position and bevel lines are treated as physical
part edges, which splits the flange plate outline into half-width bands and
defeats ``select_flange_polygons``.
"""

from __future__ import annotations

from steel_dxf_split.bh_dialect import DEFAULT_TEKLA_DIALECT, canonical_tekla_linetype
from steel_dxf_split.bh_ir import SemanticLayer, VisibilityClass


def test_dashedx2_is_classified_as_hidden_projection() -> None:
    assert DEFAULT_TEKLA_DIALECT.is_hidden_projection_linetype("DASHEDX2")
    assert (
        DEFAULT_TEKLA_DIALECT.visibility(SemanticLayer.PART_EDGE, "DASHEDX2")
        == VisibilityClass.HIDDEN
    )


def test_dashedx2_canonicalizes_to_hidden_edge_linetype() -> None:
    assert (
        canonical_tekla_linetype(VisibilityClass.HIDDEN, "DASHEDX2")
        == "XKITLINE04"
    )


def test_continuous_remains_physical() -> None:
    assert (
        DEFAULT_TEKLA_DIALECT.visibility(SemanticLayer.PART_EDGE, "Continuous")
        == VisibilityClass.PHYSICAL
    )
    assert not DEFAULT_TEKLA_DIALECT.is_hidden_projection_linetype("Continuous")
