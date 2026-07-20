"""DXF provenance and header based ODA output-version policy."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.jobs.interface import AnalysisResult
from app.platform.config.constants import TASK_DWG_TO_DXF
from app.platform.config.settings import settings

logger = logging.getLogger(__name__)

DXF_ACADVER_MAP: dict[str, str] = {
    "AC1012": "ACAD13",
    "AC1014": "ACAD14",
    "AC1015": "ACAD2000",
    "AC1018": "ACAD2004",
    "AC1021": "ACAD2007",
    "AC1024": "ACAD2010",
    "AC1027": "ACAD2013",
    "AC1032": "ACAD2018",
}

KNOWN_ODA_VERSIONS: frozenset[str] = frozenset(DXF_ACADVER_MAP.values())


def resolve_source_dwg_version(db: Session, source_file_id: int) -> str | None:
    """Return the recorded DWG version for a DXF created by this system."""
    result = db.scalars(
        select(AnalysisResult).where(
            AnalysisResult.result_file_id == source_file_id,
            AnalysisResult.result_type == TASK_DWG_TO_DXF,
        )
    ).first()
    if result and result.tool_version:
        logger.info(
            "Resolved source DWG version %s from AnalysisResult#%d for DXF file#%d",
            result.tool_version,
            result.id,
            source_file_id,
        )
        return result.tool_version
    return None


def detect_dxf_output_version(source_path: Path) -> str:
    """Map the source ``$ACADVER`` header to an ODA DWG version."""
    try:
        with source_path.open("r", encoding="utf-8", errors="ignore") as source:
            for _ in range(200):
                line = source.readline()
                if not line:
                    break
                if line.strip() == "$ACADVER":
                    source.readline()
                    value = source.readline().strip()
                    if value in DXF_ACADVER_MAP:
                        return DXF_ACADVER_MAP[value]
                    break
        return settings.dxf2dwg_converter_version
    except OSError:
        return settings.dxf2dwg_converter_version


def resolve_dwg_output_version(
    db: Session,
    source_file_id: int,
    source_path: Path,
    *,
    job_id: int | None = None,
) -> str:
    """Prefer registered provenance, then inspect the source DXF header."""
    resolved = resolve_source_dwg_version(db, source_file_id)
    if resolved and resolved in KNOWN_ODA_VERSIONS:
        return resolved
    if resolved:
        logger.warning(
            "Ignoring unknown version %r from AnalysisResult for job %s — "
            "falling back to $ACADVER detection",
            resolved,
            job_id,
        )
    return detect_dxf_output_version(source_path)
