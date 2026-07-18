"""DXF file reader using ezdxf — block expansion and entity extraction.

Reads DXF AC1032+ files, iterates anonymous blocks (*A1, *A2, ...),
and extracts TEXT / LINE entities with their coordinates.
"""

import re
from pathlib import Path

import ezdxf
from loguru import logger

from .models import LineEntity, TextEntity

_ANON_BLOCK_RE = re.compile(r"^\*A\d+$")


def read_dxf_blocks(
    filepath: Path,
) -> dict[str, tuple[list[TextEntity], list[LineEntity]]]:
    """Read a DXF file and return {block_name: (texts, lines)} for anonymous blocks.

    Args:
        filepath: Path to a .dxf file.

    Returns:
        Dict mapping block name (e.g. "*A2") to (list of TextEntity, list of LineEntity).
    """
    try:
        doc = ezdxf.readfile(str(filepath))
    except Exception as exc:
        logger.error(f"Failed to read DXF {filepath}: {exc}")
        return {}

    result: dict[str, tuple[list[TextEntity], list[LineEntity]]] = {}

    for block in doc.blocks:
        block_name = block.name
        if not _ANON_BLOCK_RE.match(block_name):
            continue

        texts = _extract_text_entities(block)
        lines = _extract_line_entities(block)

        if texts or lines:
            result[block_name] = (texts, lines)

    return result


def _extract_text_entities(block) -> list[TextEntity]:
    """Extract TEXT entities from an ezdxf block layout."""
    result: list[TextEntity] = []
    try:
        for entity in block.query("TEXT"):
            try:
                insert = entity.dxf.insert
                result.append(
                    TextEntity(
                        x=float(insert.x),
                        y=float(insert.y),
                        text=str(entity.dxf.text),
                        height=float(entity.dxf.height),
                        layer=str(entity.dxf.layer),
                    )
                )
            except Exception as exc:
                logger.debug(f"Skipping malformed TEXT entity: {exc}")
    except Exception as exc:
        logger.debug(f"Error querying TEXT in block: {exc}")
    return result


def _extract_line_entities(block) -> list[LineEntity]:
    """Extract LINE entities from an ezdxf block layout."""
    result: list[LineEntity] = []
    try:
        for entity in block.query("LINE"):
            try:
                start = entity.dxf.start
                end = entity.dxf.end
                result.append(
                    LineEntity(
                        x1=float(start.x),
                        y1=float(start.y),
                        x2=float(end.x),
                        y2=float(end.y),
                        layer=str(entity.dxf.layer),
                    )
                )
            except Exception as exc:
                logger.debug(f"Skipping malformed LINE entity: {exc}")
    except Exception as exc:
        logger.debug(f"Error querying LINE in block: {exc}")
    return result


def get_codepage(filepath: Path) -> str:
    """Read the $DWGCODEPAGE header from a DXF file.

    Returns the codepage string (e.g. "ANSI_936") or empty string if not found.
    """
    try:
        doc = ezdxf.readfile(str(filepath))
        return doc.header.get("$DWGCODEPAGE", "")
    except Exception:
        return ""


def get_acadver(filepath: Path) -> str:
    """Read the $ACADVER header from a DXF file.

    Returns the version string (e.g. "AC1032") or empty string.
    """
    try:
        doc = ezdxf.readfile(str(filepath))
        return doc.header.get("$ACADVER", "")
    except Exception:
        return ""


# ---- INSERT transform detection (v2) ----

def detect_insert_transforms(
    filepath: Path,
    table_block_name: str | None = None,
) -> dict:
    """Detect INSERT entities with non-default transforms.

    Scans ENTITIES section for INSERTs with non-zero position,
    non-unit scale, or non-zero rotation.

    Returns dict:
        total_inserts, transformed_inserts,
        table_insert_transformed (bool),
        table_insert_details (str or None)
    """
    try:
        doc = ezdxf.readfile(str(filepath))
    except Exception:
        return {
            "total_inserts": 0,
            "transformed_inserts": 0,
            "table_insert_transformed": False,
            "table_insert_details": None,
        }

    msp = doc.modelspace()
    total = 0
    transformed = 0
    table_transformed = False
    table_details = None

    for entity in msp.query("INSERT"):
        total += 1
        try:
            ins = entity.dxf.insert
            sx = entity.dxf.xscale if hasattr(entity.dxf, 'xscale') else 1.0
            sy = entity.dxf.yscale if hasattr(entity.dxf, 'yscale') else 1.0
            rot = entity.dxf.rotation if hasattr(entity.dxf, 'rotation') else 0.0
            name = entity.dxf.name

            is_transformed = (
                abs(ins.x) > 0.01
                or abs(ins.y) > 0.01
                or abs(sx - 1.0) > 0.001
                or abs(sy - 1.0) > 0.001
                or abs(rot) > 0.001
            )

            if is_transformed:
                transformed += 1

            if table_block_name and name == table_block_name and is_transformed:
                table_transformed = True
                table_details = (
                    f"INSERT {name}: x={ins.x:.1f} y={ins.y:.1f} "
                    f"sx={sx:.3f} sy={sy:.3f} rot={rot:.3f}"
                )
        except Exception:
            pass

    return {
        "total_inserts": total,
        "transformed_inserts": transformed,
        "table_insert_transformed": table_transformed,
        "table_insert_details": table_details,
    }
