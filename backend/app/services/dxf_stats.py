"""Lightweight DXF file statistics — no external dependencies beyond stdlib.

Used by both DWG→DXF and DXF→DWG pipelines to capture entity counts, section
sizes, and other fidelity metrics without pulling in ezdxf at conversion time.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Entity type markers we care about (group code 0 values inside ENTITIES / BLOCKS).
_TRACKED_ENTITIES: set[str] = {
    "LINE",
    "CIRCLE",
    "ARC",
    "TEXT",
    "MTEXT",
    "INSERT",
    "POINT",
    "ELLIPSE",
    "SPLINE",
    "HATCH",
    "LWPOLYLINE",
    "POLYLINE",
    "DIMENSION",
    "ARC_DIMENSION",
    "LEADER",
    "MLINE",
    "ATTDEF",
    "ATTRIB",
    "SOLID",
    "3DFACE",
    "IMAGE",
    "VIEWPORT",
    "XLINE",
    "RAY",
    "TABLE",
    "MESH",
    "SURFACE",
    "REGION",
    "BODY",
    "3DSOLID",
    "WIPEOUT",
}

# Section names.
_SECTION_MARKERS: frozenset[str] = frozenset(
    {"HEADER", "CLASSES", "TABLES", "BLOCKS", "ENTITIES", "OBJECTS", "THUMBNAILIMAGE"}
)


def _count_dxf_stats(path: Path) -> dict:
    """Scan a DXF file and return statistics dict.

    The returned dict is small enough to store in job_steps output_json
    or AnalysisResult.result_json without blowing up row sizes.
    """
    stats: dict = {
        "dxf_size_bytes": int(path.stat().st_size),
        "sections": {},
        "entity_counts": {},
        "total_entities": 0,
    }
    current_section: str | None = None
    in_entities: bool = False  # True inside ENTITIES or BLOCKS section body
    prev_line: str = ""

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                line = raw.rstrip("\n\r")

                # Track section boundaries.
                if prev_line.strip() == "2" and line.strip() in _SECTION_MARKERS:
                    current_section = line.strip()
                    stats["sections"].setdefault(current_section, 0)

                if current_section is not None:
                    stats["sections"][current_section] += 1

                # We are inside an entity definition when the previous line is
                # group code 0 and we are inside ENTITIES or BLOCKS.
                if prev_line.strip() == "0" and current_section in ("ENTITIES", "BLOCKS"):
                    in_entities = True
                elif prev_line.strip() == "0":
                    in_entities = False

                # Count tracked entity types.
                if in_entities and line.strip() in _TRACKED_ENTITIES:
                    key = line.strip()
                    stats["entity_counts"][key] = int(stats["entity_counts"].get(key, 0)) + 1
                    stats["total_entities"] += 1
                    in_entities = False  # next 0/x starts a new entry

                prev_line = line

    except Exception:
        logger.warning("Unable to count DXF stats for %s", path, exc_info=True)
        stats["error"] = "stats unavailable"

    # Drop empty sections dict keys (from the setdefault above).
    stats["sections"] = {k: v for k, v in stats["sections"].items() if v > 0}

    return stats


def dxf_entity_summary(stats: dict) -> str:
    """Return a compact one-line summary string for logging."""
    total = stats.get("total_entities", 0)
    top = sorted(stats.get("entity_counts", {}).items(), key=lambda x: -x[1])[:5]
    parts = [f"{etype}={count}" for etype, count in top]
    return f"total={total} " + " ".join(parts) if parts else f"total={total}"
