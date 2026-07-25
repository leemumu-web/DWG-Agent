from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from ezdxf.fonts import fonts

from steel_dxf_split.box import artifact_io, preview


_WINDOWS_CJK_FONTS = ("simsun.ttc", "msyh.ttc", "simhei.ttf")


@pytest.fixture(autouse=True)
def _adapt_posix_directory_fsync_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    if not hasattr(os, "O_DIRECTORY"):
        monkeypatch.setattr(artifact_io, "fsync_directory", lambda _path: None)
    yield


@pytest.fixture(autouse=True)
def _adapt_upstream_preview_fonts_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    if os.name != "nt":
        yield
        return
    select_upstream_font = preview.select_cjk_fallback_font

    def select_host_font() -> str:
        try:
            return select_upstream_font()
        except RuntimeError:
            for candidate in _WINDOWS_CJK_FONTS:
                if fonts.font_manager.has_font(candidate):
                    return candidate
            raise

    monkeypatch.setattr(
        preview,
        "select_cjk_fallback_font",
        select_host_font,
    )
    yield
