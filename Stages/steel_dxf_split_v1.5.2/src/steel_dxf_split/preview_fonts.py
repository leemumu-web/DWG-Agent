"""Shared host-font selection for non-authoritative DXF previews."""

from __future__ import annotations

from collections.abc import Sequence

from ezdxf.fonts import fonts


_FONT_SCAN_ATTEMPTED = False


def select_cjk_fallback_font(candidates: Sequence[str]) -> str:
    """Return an installed CJK font after one stale-index refresh."""

    global _FONT_SCAN_ATTEMPTED

    names = tuple(candidates)
    manager = fonts.font_manager
    for candidate in names:
        if manager.has_font(candidate):
            return candidate

    # ezdxf loads a persistent font index at import time. A valid system font
    # installed after that index was created is invisible until it is rebuilt.
    if not _FONT_SCAN_ATTEMPTED:
        manager.build()
        _FONT_SCAN_ATTEMPTED = True
        for candidate in names:
            if manager.has_font(candidate):
                return candidate

    raise RuntimeError(
        "DXF preview requires an installed CJK font: " + ", ".join(names)
    )
