"""DWG header to ODA output-version policy."""

from __future__ import annotations

from pathlib import Path

from app.platform.config.settings import settings

# Matching the source generation avoids gratuitous format upgrades and limits
# round-trip loss. ODA uses these names at its process boundary.
DWG_VERSION_MAP: dict[bytes, str] = {
    b"AC1012": "ACAD13",
    b"AC1014": "ACAD14",
    b"AC1015": "ACAD2000",
    b"AC1018": "ACAD2004",
    b"AC1021": "ACAD2007",
    b"AC1024": "ACAD2010",
    b"AC1027": "ACAD2013",
    b"AC1032": "ACAD2018",
}

KNOWN_ODA_VERSIONS: frozenset[str] = frozenset(DWG_VERSION_MAP.values())


def detect_dwg_output_version(source_path: Path) -> str:
    """Return the matching ODA version, or the configured safe fallback."""
    try:
        with source_path.open("rb") as source:
            header = source.read(6)
        return DWG_VERSION_MAP.get(header, settings.oda_converter_version)
    except OSError:
        return settings.oda_converter_version
