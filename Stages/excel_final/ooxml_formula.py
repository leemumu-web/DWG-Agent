"""Patch cached values for formulas in an OOXML workbook.

openpyxl deliberately writes formulas without calculating them.  The normalized
Excel Final output needs both the formula and an immediately readable cached
value, so this module performs the small, explicit OOXML post-processing step.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
import os
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True, slots=True)
class FormulaCache:
    formula: str
    value: Decimal | int | float | None


def _sheet_part(archive: ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationship_id: str | None = None
    for sheet in workbook.findall(f".//{{{_MAIN_NS}}}sheet"):
        if sheet.get("name") == sheet_name:
            relationship_id = sheet.get(f"{{{_REL_NS}}}id")
            break
    if relationship_id is None:
        raise KeyError(f"workbook has no sheet named {sheet_name!r}")

    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target: str | None = None
    for relationship in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship"):
        if relationship.get("Id") == relationship_id:
            target = relationship.get("Target")
            break
    if not target:
        raise ValueError(f"sheet {sheet_name!r} has no OOXML relationship target")

    if target.startswith("/"):
        return target.lstrip("/")
    return str(PurePosixPath("xl") / target)


def _numeric_text(value: Decimal | int | float) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("formula cache must be a finite number")
        return format(value, "f")
    decimal_value = Decimal(str(value))
    if not decimal_value.is_finite():
        raise ValueError("formula cache must be a finite number")
    return format(decimal_value, "f")


def _patched_sheet(xml: bytes, caches: dict[str, FormulaCache]) -> bytes:
    ET.register_namespace("", _MAIN_NS)
    root = ET.fromstring(xml)
    cells = {
        cell.get("r"): cell
        for cell in root.findall(f".//{{{_MAIN_NS}}}c")
        if cell.get("r")
    }
    for coordinate, cache in caches.items():
        cell = cells.get(coordinate)
        if cell is None:
            raise KeyError(f"formula cell {coordinate!r} does not exist")
        formula = cell.find(f"{{{_MAIN_NS}}}f")
        if formula is None:
            formula = ET.Element(f"{{{_MAIN_NS}}}f")
            cell.insert(0, formula)
        formula.text = cache.formula.removeprefix("=")

        value = cell.find(f"{{{_MAIN_NS}}}v")
        if value is None:
            value = ET.SubElement(cell, f"{{{_MAIN_NS}}}v")
        value.text = (
            None if cache.value is None else _numeric_text(cache.value)
        )
        cell.attrib.pop("t", None)
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


def _write_entry(destination: ZipFile, info: ZipInfo, payload: bytes) -> None:
    # Passing the original ZipInfo preserves timestamps, permissions and comments.
    destination.writestr(info, payload)


def patch_formula_caches(
    workbook_path: str | Path,
    sheet_name: str,
    caches: dict[str, FormulaCache],
) -> None:
    """Atomically add cached numeric values to formula cells on one worksheet."""
    if not caches:
        return
    path = Path(workbook_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(temp_fd)
    temp_path = Path(temp_name)
    try:
        with ZipFile(path, "r") as source:
            sheet_part = _sheet_part(source, sheet_name)
            if sheet_part not in source.namelist():
                raise ValueError(f"OOXML sheet part is missing: {sheet_part}")
            with ZipFile(temp_path, "w", compression=ZIP_DEFLATED) as destination:
                for info in source.infolist():
                    payload = source.read(info.filename)
                    if info.filename == sheet_part:
                        payload = _patched_sheet(payload, caches)
                    _write_entry(destination, info, payload)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
