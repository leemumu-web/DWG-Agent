from __future__ import annotations

from io import StringIO

import ezdxf
import pytest

from app.core.exceptions import AppHTTPException
from app.services import dxf_preview_service as service


def _dxf_bytes(*, block_lines: int = 0) -> bytes:
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    msp.add_line((0, 0), (120, 40), dxfattribs={"layer": "0"})
    msp.add_circle((40, 20), 12, dxfattribs={"layer": "0"})
    if block_lines:
        block = doc.blocks.new(name="PREVIEW_TEST_BLOCK")
        for index in range(block_lines):
            block.add_line((index, 0), (index, 10))
        msp.add_blockref("PREVIEW_TEST_BLOCK", (10, 10))
    stream = StringIO()
    doc.write(stream)
    return stream.getvalue().encode(doc.output_encoding, errors="replace")


def test_render_dxf_returns_safe_svg_and_metadata() -> None:
    rendered = service.render_dxf_to_svg(_dxf_bytes(block_lines=3))

    lower = rendered.payload.lower()
    assert rendered.payload.startswith(b"<?xml")
    assert b"<svg" in lower
    assert rendered.content_type == "image/svg+xml"
    assert rendered.document_entities >= rendered.modelspace_entities >= 3
    assert rendered.entity_counts["LINE"] >= 1
    assert "0" in rendered.layers
    assert rendered.bounds.max_x > rendered.bounds.min_x
    assert rendered.bounds.max_y > rendered.bounds.min_y
    assert not any(token in lower for token in service.FORBIDDEN_SVG_TOKENS)


def test_document_entity_limit_counts_entities_outside_modelspace(monkeypatch) -> None:
    baseline = service.inspect_dxf(_dxf_bytes(block_lines=12))
    assert baseline.document_entities > baseline.modelspace_entities
    monkeypatch.setattr(
        service,
        "MAX_DXF_ENTITIES",
        baseline.modelspace_entities,
    )

    with pytest.raises(AppHTTPException) as exc:
        service.render_dxf_to_svg(_dxf_bytes(block_lines=12))

    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "DXF_TOO_COMPLEX"


def test_output_size_limit_is_enforced(monkeypatch) -> None:
    monkeypatch.setattr(service, "MAX_PREVIEW_BYTES", 32)

    with pytest.raises(AppHTTPException) as exc:
        service.render_dxf_to_svg(_dxf_bytes())

    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "DXF_PREVIEW_TOO_LARGE"


def test_invalid_dxf_has_public_error() -> None:
    with pytest.raises(AppHTTPException) as exc:
        service.render_dxf_to_svg(b"not-a-dxf")

    assert exc.value.status_code == 415
    assert exc.value.detail["code"] == "DXF_PARSE_ERROR"
    assert "Traceback" not in exc.value.detail["message"]


def test_declared_source_size_limit_is_checked() -> None:
    with pytest.raises(AppHTTPException) as exc:
        service.validate_dxf_source_size(service.MAX_DXF_SIZE_BYTES + 1)

    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "DXF_TOO_LARGE"
