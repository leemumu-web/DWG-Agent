"""Single-view BH drawings route to an auditable rejection, not a job failure.

A complete BH single-part drawing carries an elevation view and a flange-plane
view.  When a Tekla export contains only the elevation view, no flange plate
can be reconstructed.  This used to raise a plain ``ValueError`` that escaped
the pipeline as an un-reported ``failed`` task; it must instead raise the
classified domain error so the drawing is preserved for review with a
diagnostic code.
"""

from __future__ import annotations

import ezdxf
import pytest
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_errors import BHInsufficientViewError
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT


def _elevation_block(doc: ezdxf.document.Drawing, name: str) -> None:
    """Full member elevation: outline, flange lines and a web line."""
    block = doc.blocks.new(name=name)
    lines = (
        ((0.0, 0.0), (900.0, 0.0)),
        ((900.0, 0.0), (900.0, 700.0)),
        ((900.0, 700.0), (0.0, 700.0)),
        ((0.0, 700.0), (0.0, 0.0)),
        ((0.0, 25.0), (900.0, 25.0)),
        ((0.0, 675.0), (900.0, 675.0)),
        ((0.0, 25.0), (0.0, 675.0)),
    )
    for start, end in lines:
        block.add_line(start, end, dxfattribs={"layer": "Part", "linetype": "Continuous"})


def _flange_block(doc: ezdxf.document.Drawing, name: str, hidden_linetype: str) -> None:
    """Flange-plane view: 900 x 350 rectangle with a web-position hidden line."""
    block = doc.blocks.new(name=name)
    lines = (
        ((0.0, 0.0), (900.0, 0.0)),
        ((900.0, 0.0), (900.0, 350.0)),
        ((900.0, 350.0), (0.0, 350.0)),
        ((0.0, 350.0), (0.0, 0.0)),
        ((0.0, 175.0), (900.0, 175.0)),  # hidden web-position line
    )
    for index, (start, end) in enumerate(lines):
        linetype = hidden_linetype if index == 4 else "Continuous"
        block.add_line(start, end, dxfattribs={"layer": "Part", "linetype": linetype})


def _title_block(doc: ezdxf.document.Drawing) -> None:
    block = doc.blocks.new(name="*TITLE")
    for text, pos in (
        ("FJ-Q3-TEST", (450.0, 780.0)),
        ("BH700*350*18*25", (450.0, 750.0)),
        ("900", (450.0, -50.0)),
    ):
        block.add_text(
            text,
            dxfattribs={"layer": "OtherObjectType", "insert": pos, "height": 10},
        )


def _drawing(views: list[tuple[str, str, str]]) -> ezdxf.document.Drawing:
    """Build a minimal BH drawing; each entry is (block_name, kind, linetype).

    ``kind`` is either ``"elevation"`` or ``"flange"``; ``linetype`` is the
    hidden-edge spelling used for the flange web-position line.
    """
    doc = ezdxf.new(dxfversion="AC1015")
    for name, kind, linetype in views:
        if kind == "elevation":
            _elevation_block(doc, name)
        else:
            _flange_block(doc, name, linetype)
    _title_block(doc)
    for name, _, _ in views:
        doc.modelspace().add_blockref(name, (0, 0))
    doc.modelspace().add_blockref("*TITLE", (0, 0))
    return doc


def test_single_view_raises_insufficient_view_domain_error() -> None:
    doc = _drawing([("*A4", "elevation", "Continuous")])
    with pytest.raises(BHInsufficientViewError) as exc_info:
        compile_bh_document(
            doc,
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            source_path=None,
        )
    assert "flange-plane view is missing" in str(exc_info.value)
    assert exc_info.value.diagnostic_code == "BH-INSUFFICIENT-PART-VIEWS"


def test_single_view_with_dashedx2_hidden_linetype_still_routes() -> None:
    doc = _drawing([("*A4", "flange", "DASHEDX2")])
    with pytest.raises(BHInsufficientViewError):
        compile_bh_document(
            doc,
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            source_path=None,
        )


def test_two_views_do_not_raise_insufficient_view() -> None:
    doc = _drawing(
        [("*A4", "elevation", "Continuous"), ("*A8", "flange", "DASHEDX2")]
    )
    try:
        compile_bh_document(
            doc,
            source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
            source_path=None,
        )
    except BHInsufficientViewError as error:  # pragma: no cover - guard
        pytest.fail(f"two-view drawing must not raise BHInsufficientViewError: {error}")
    except Exception:
        # A two-view drawing may still be rejected for geometry or metadata
        # reasons; only the insufficient-view signal is under test here.
        pass
