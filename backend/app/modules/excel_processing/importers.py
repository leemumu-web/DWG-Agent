"""Streaming workbook-to-MySQL projection for Excel Final results."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import openpyxl
from sqlalchemy.orm import Session

from app.modules.excel_processing.models import ExcelFinalComponent, ExcelFinalPart
from app.modules.excel_processing.schemas import ComponentsImportStats, PartsImportStats


def _canonical_header(value: object) -> str:
    text = "" if value is None else str(value).strip().replace(" ", "")
    return re.split(r"[（(]", text, maxsplit=1)[0]


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _value(row: list[object], column: int | None) -> object:
    return row[column - 1] if column is not None and column <= len(row) else None


def import_parts_to_db(
    db: Session,
    batch_id: int,
    output_path: Path,
) -> PartsImportStats:
    """Stream the canonical part-list sheet into `excel_final_parts`."""
    workbook = openpyxl.load_workbook(output_path, read_only=True, data_only=True)
    try:
        sheet_name = next(
            (name for name in ("整理表", "整理表_拆板后") if name in workbook.sheetnames),
            None,
        )
        if sheet_name is None:
            return {"parts_imported": 0, "error": "No 整理表 sheet found"}

        rows = workbook[sheet_name].iter_rows(values_only=True)
        columns: dict[str, int] = {}
        for column, value in enumerate(next(rows, ()), start=1):
            columns.setdefault(_canonical_header(value), column)

        def column(*names: str) -> int | None:
            return next((columns[name] for name in names if name in columns), None)

        seq_col = column("序号")
        component_no_col = column("构件编号")
        component_qty_col = column("构件数")
        part_type_col = column("类型")
        part_no_col = column("零件号", "零件编号")
        profile_spec_col = column("截面型材")
        spec_col = column("规格")
        width_col = column("宽度")
        length_col = column("长度")
        left_inset_col = column("左进")
        right_inset_col = column("右进")
        cut_length_col = column("下料长度")
        material_col = column("材质")
        qty_col = column("数量")
        total_qty_col = column("总数")
        total_length_col = column("总长")
        density_col = column("比重")
        theo_unit_weight_col = column("理单重")
        theo_total_weight_col = column("理总重")
        net_unit_weight_col = column("单净重")
        net_total_weight_col = column("总净重")
        table_net_weight_col = column("表净重")
        gross_unit_weight_col = column("单毛重")
        gross_total_weight_col = column("总毛重")
        table_gross_weight_col = column("表毛重")
        surface_area_col = column("单表面积")
        total_surface_area_col = column("总表面积")

        required_columns = {
            "序号": seq_col,
            "构件编号": component_no_col,
            "零件号": part_no_col,
            "规格": spec_col,
            "长度": length_col,
            "材质": material_col,
            "数量": qty_col,
        }
        missing = [name for name, position in required_columns.items() if position is None]
        if missing:
            raise ValueError(
                "Excel Final output is missing required columns: " + ", ".join(missing)
            )

        parts: list[dict[str, Any]] = []
        for row_number, values in enumerate(rows, start=2):
            row = list(values)
            if all(value is None for value in row):
                continue
            part_no = _text(_value(row, part_no_col))
            summary_values = [
                _text(_value(row, position))
                for position in (component_no_col, part_no_col, profile_spec_col, spec_col)
                if position is not None
            ]
            if not part_no or any(value and value.startswith("合计") for value in summary_values):
                continue

            seq = _number(_value(row, seq_col))
            component_qty = _number(_value(row, component_qty_col))
            parts.append(
                {
                    "batch_id": batch_id,
                    "seq": int(seq or 0) if seq_col else row_number - 1,
                    "component_no": _text(_value(row, component_no_col)),
                    "component_qty": (
                        int(component_qty) if component_qty is not None else None
                    ),
                    "part_type": _text(_value(row, part_type_col)),
                    "part_no": part_no,
                    "profile_spec": _text(_value(row, profile_spec_col)),
                    "spec": _text(_value(row, spec_col)),
                    "width": _number(_value(row, width_col)),
                    "length": _number(_value(row, length_col)),
                    "left_inset": _number(_value(row, left_inset_col)),
                    "right_inset": _number(_value(row, right_inset_col)),
                    "cut_length": _number(_value(row, cut_length_col)),
                    "material": _text(_value(row, material_col)),
                    "qty": _number(_value(row, qty_col)),
                    "total_qty": _number(_value(row, total_qty_col)),
                    "total_length": _number(_value(row, total_length_col)),
                    "density": _number(_value(row, density_col)),
                    "theo_unit_weight": _number(_value(row, theo_unit_weight_col)),
                    "theo_total_weight": _number(_value(row, theo_total_weight_col)),
                    "net_unit_weight": _number(_value(row, net_unit_weight_col)),
                    "net_total_weight": _number(_value(row, net_total_weight_col)),
                    "table_net_weight": _number(_value(row, table_net_weight_col)),
                    "gross_unit_weight": _number(_value(row, gross_unit_weight_col)),
                    "gross_total_weight": _number(_value(row, gross_total_weight_col)),
                    "table_gross_weight": _number(_value(row, table_gross_weight_col)),
                    "surface_area": _number(_value(row, surface_area_col)),
                    "total_surface_area": _number(_value(row, total_surface_area_col)),
                }
            )
    finally:
        workbook.close()

    if parts:
        db.bulk_insert_mappings(ExcelFinalPart, parts)
        db.flush()
    return {"parts_imported": len(parts)}


def import_components_to_db(
    db: Session,
    batch_id: int,
    output_path: Path,
) -> ComponentsImportStats:
    """Stream the component-summary sheet into `excel_final_components`."""
    workbook = openpyxl.load_workbook(output_path, read_only=True, data_only=True)
    try:
        if "构件表" not in workbook.sheetnames:
            return {"components_imported": 0}
        rows = workbook["构件表"].iter_rows(values_only=True)
        headers = [str(value or "") for value in next(rows, ())]
        component_no_col = next(
            (index for index, header in enumerate(headers) if "构件编号" in header),
            None,
        )
        component_qty_col = next(
            (index for index, header in enumerate(headers) if "构件数" in header),
            None,
        )
        weight_col = next(
            (
                index
                for index, header in enumerate(headers)
                if "总净重" in header or "总重" in header
            ),
            None,
        )
        if component_no_col is None:
            raise ValueError("Excel Final component sheet is missing required column: 构件编号")

        components: list[dict[str, Any]] = []
        for values in rows:
            component_no = str(values[component_no_col] or "").strip()
            if not component_no or "合计" in component_no:
                continue
            qty = _number(values[component_qty_col]) if component_qty_col is not None else None
            weight = _number(values[weight_col]) if weight_col is not None else None
            components.append(
                {
                    "batch_id": batch_id,
                    "component_no": component_no,
                    "component_qty": int(qty) if qty is not None else None,
                    "total_weight": weight,
                }
            )
    finally:
        workbook.close()

    if components:
        db.bulk_insert_mappings(ExcelFinalComponent, components)
        db.flush()
    return {"components_imported": len(components)}


__all__ = ["import_components_to_db", "import_parts_to_db"]
