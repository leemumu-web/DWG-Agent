"""Step 0-1: Load .xls (TSV or space-delimited), detect encoding, find header,
create workbook.

Produces an openpyxl Workbook with two sheets:
  - 原表 (preserved original, cleaned)
  - 整理表 (working copy)
"""

from __future__ import annotations

import logging
import re as _re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
import openpyxl

from config import KW_批次, KW_构件编号, KW_零件号, KW_数量, KW_材质
from domain import ComponentRowKind, ComponentSourceRow, SourcePart
from input_contract import HeaderDetection, InputKind, detect_canonical_header, inspect_production_input
from quality import IssueLevel, QualityIssue
from utils import safe_str

log = logging.getLogger(__name__)

# Keywords for detecting steel-table content (encoding confirmation)
_CONTENT_KWS = ["构件编号", "零件", "规格", "长度", "材质", "数量", "型材", "型 材"]


@dataclass(frozen=True, slots=True)
class CanonicalWorkbookRead:
    source_path: Path
    sheet_name: str
    header: HeaderDetection
    working_values: tuple[tuple[Any, ...], ...]
    parts: tuple[SourcePart, ...]
    component_rows: tuple[ComponentSourceRow, ...]
    issues: tuple[QualityIssue, ...]


def _working_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace(" ", "").replace("　", "")
    return value


def _row_value(row: tuple[Any, ...], columns: dict[str, int] | Any, field: str) -> Any:
    column = columns.get(field)
    if column is None or column > len(row):
        return None
    return row[column - 1]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value)
    return result if result else None


def _decimal(value: Any, *, field: str, source_row: int) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"row {source_row} field {field} is not numeric: {value!r}") from exc


def _has_part_payload(row: tuple[Any, ...], columns: Any) -> bool:
    return any(
        _row_value(row, columns, field) not in (None, "")
        for field in (
            "规格", "零件长度", "材质", "数量", "单净重", "总净重",
            "单毛重", "总毛重", "单表面积", "总表面积",
        )
    )


def _component_source_row(
    row: tuple[Any, ...],
    columns: Any,
    *,
    sheet_name: str,
    source_row: int,
    kind: ComponentRowKind,
) -> ComponentSourceRow:
    component_no = _text(_row_value(row, columns, "构件编号"))
    if not component_no:
        raise ValueError(f"row {source_row} component source row has no component number")
    return ComponentSourceRow(
        source_sheet=sheet_name,
        source_row=source_row,
        kind=kind,
        batch=_text(_row_value(row, columns, "批次")),
        component_no=component_no,
        component_qty=_decimal(_row_value(row, columns, "数量"), field="数量", source_row=source_row),
        original_spec=_text(_row_value(row, columns, "规格")),
        material=_text(_row_value(row, columns, "材质")),
        source_unit_net=_decimal(_row_value(row, columns, "单净重"), field="单净重", source_row=source_row),
        source_total_net=_decimal(_row_value(row, columns, "总净重"), field="总净重", source_row=source_row),
        source_unit_gross=_decimal(_row_value(row, columns, "单毛重"), field="单毛重", source_row=source_row),
        source_total_gross=_decimal(_row_value(row, columns, "总毛重"), field="总毛重", source_row=source_row),
        source_unit_area=_decimal(_row_value(row, columns, "单表面积"), field="单表面积", source_row=source_row),
        source_total_area=_decimal(_row_value(row, columns, "总表面积"), field="总表面积", source_row=source_row),
        component_length=_decimal(_row_value(row, columns, "构件长度"), field="构件长度", source_row=source_row),
        component_width=_decimal(_row_value(row, columns, "构件宽度"), field="构件宽度", source_row=source_row),
        component_height=_decimal(_row_value(row, columns, "构件高度"), field="构件高度", source_row=source_row),
    )


_COMPONENT_IDENTITY_FIELDS = ("batch", "component_qty", "original_spec", "material")
_COMPONENT_METRIC_FIELDS = (
    "source_unit_net",
    "source_total_net",
    "source_unit_gross",
    "source_total_gross",
    "source_unit_area",
    "source_total_area",
    "component_length",
    "component_width",
    "component_height",
)


def _summarize_component_rows(
    rows: list[ComponentSourceRow],
) -> tuple[tuple[ComponentSourceRow, ...], tuple[QualityIssue, ...]]:
    """Collapse Tekla start/subtotal records into one canonical component row."""
    grouped: dict[str, list[ComponentSourceRow]] = {}
    for row in rows:
        grouped.setdefault(row.component_no, []).append(row)

    summaries: list[ComponentSourceRow] = []
    issues: list[QualityIssue] = []
    for component_no, group in grouped.items():
        starts = [row for row in group if row.kind == ComponentRowKind.START]
        subtotals = [row for row in group if row.kind == ComponentRowKind.SUBTOTAL]
        base = starts[0] if starts else group[0]
        values: dict[str, object | None] = {}

        for field in (*_COMPONENT_IDENTITY_FIELDS, *_COMPONENT_METRIC_FIELDS):
            preferred = (
                [*starts, *subtotals]
                if field in _COMPONENT_IDENTITY_FIELDS
                else [*subtotals, *starts]
            )
            present = [getattr(row, field) for row in preferred if getattr(row, field) is not None]
            selected = present[0] if present else None
            values[field] = selected
            conflicts = [value for value in present[1:] if value != selected]
            if conflicts:
                conflict_row = next(
                    row
                    for row in preferred
                    if getattr(row, field) is not None and getattr(row, field) != selected
                )
                issues.append(QualityIssue(
                    level=IssueLevel.SEVERE,
                    category="构件编号冲突",
                    source_sheet=conflict_row.source_sheet,
                    source_row=conflict_row.source_row,
                    component_no=component_no,
                    part_no=None,
                    spec=base.original_spec,
                    field=field,
                    actual_value=getattr(conflict_row, field),
                    expected_value=selected,
                    absolute_error=None,
                    relative_error=None,
                    affects_part=True,
                    density_source=None,
                    description=(
                        f"构件编号 {component_no} 的字段 {field} 在来源行中不一致"
                    ),
                ))

        summaries.append(ComponentSourceRow(
            source_sheet=base.source_sheet,
            source_row=base.source_row,
            kind=ComponentRowKind.SUMMARY,
            component_no=component_no,
            subtotal_source_row=subtotals[0].source_row if subtotals else None,
            **values,
        ))
    return tuple(summaries), tuple(issues)


def _canonicalize_values(
    *,
    source_path: Path,
    sheet_name: str,
    header: HeaderDetection,
    working_values: tuple[tuple[Any, ...], ...],
) -> CanonicalWorkbookRead:
    columns = header.columns
    parts: list[SourcePart] = []
    component_rows: list[ComponentSourceRow] = []
    issues: list[QualityIssue] = []
    current: ComponentSourceRow | None = None

    for source_row, row in enumerate(working_values[header.row_number:], start=header.row_number + 1):
        batch = _text(_row_value(row, columns, "批次"))
        component_no = _text(_row_value(row, columns, "构件编号"))
        part_no = _text(_row_value(row, columns, "零件号"))

        if component_no and "合计" in component_no and not part_no:
            continue
        if component_no and part_no == "构件小计":
            subtotal = _component_source_row(
                row,
                columns,
                sheet_name=sheet_name,
                source_row=source_row,
                kind=ComponentRowKind.SUBTOTAL,
            )
            component_rows.append(subtotal)
            continue
        if component_no and not part_no:
            start = _component_source_row(
                row,
                columns,
                sheet_name=sheet_name,
                source_row=source_row,
                kind=ComponentRowKind.START,
            )
            component_rows.append(start)
            current = start
            continue
        if not part_no and (current is None or not _has_part_payload(row, columns)):
            continue
        if current is None:
            raise ValueError(f"row {source_row} part {part_no!r} has no preceding component row")

        length = _decimal(_row_value(row, columns, "零件长度"), field="零件长度", source_row=source_row)
        quantity = _decimal(_row_value(row, columns, "数量"), field="数量", source_row=source_row)
        original_spec = _text(_row_value(row, columns, "规格"))
        material = _text(_row_value(row, columns, "材质"))
        invalid_fields = tuple(
            field
            for field, missing in (
                ("零件号", not part_no),
                ("规格", not original_spec),
                ("长度", length is None),
                ("材质", not material),
                ("数量", quantity is None),
                ("构件数", current.component_qty is None),
            )
            if missing
        )
        parts.append(SourcePart(
            source_sheet=sheet_name,
            source_row=source_row,
            source_seq=source_row - header.row_number,
            batch=batch or current.batch,
            component_no=current.component_no,
            component_qty=current.component_qty or Decimal("0"),
            part_no=part_no or "",
            original_spec=original_spec or "",
            material=material or "",
            length=length or Decimal("0"),
            original_qty=quantity or Decimal("0"),
            source_unit_net=_decimal(_row_value(row, columns, "单净重"), field="单净重", source_row=source_row),
            source_total_net=_decimal(_row_value(row, columns, "总净重"), field="总净重", source_row=source_row),
            source_unit_gross=_decimal(_row_value(row, columns, "单毛重"), field="单毛重", source_row=source_row),
            source_total_gross=_decimal(_row_value(row, columns, "总毛重"), field="总毛重", source_row=source_row),
            source_unit_area=_decimal(_row_value(row, columns, "单表面积"), field="单表面积", source_row=source_row),
            source_total_area=_decimal(_row_value(row, columns, "总表面积"), field="总表面积", source_row=source_row),
            classification=None,
            invalid_fields=invalid_fields,
        ))

    component_summaries, component_issues = _summarize_component_rows(component_rows)
    issues.extend(component_issues)
    return CanonicalWorkbookRead(
        source_path=source_path,
        sheet_name=sheet_name,
        header=header,
        working_values=working_values,
        parts=tuple(parts),
        component_rows=component_summaries,
        issues=tuple(issues),
    )


def _worksheet_values(worksheet: Any) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        tuple(_working_value(value) for value in row)
        for row in worksheet.iter_rows(values_only=True)
    )


def read_canonical_workbook(path: str | Path) -> CanonicalWorkbookRead:
    """Read one reviewed worksheet into immutable canonical source records."""
    inspected = inspect_production_input(Path(path))
    if inspected.kind is not InputKind.WORKBOOK or inspected.sheet_name is None:
        raise ValueError("canonical workbook reader requires a one-sheet .xlsx/.xlsm source")

    workbook = openpyxl.load_workbook(inspected.path, read_only=True, data_only=False)
    try:
        worksheet = workbook[inspected.sheet_name]
        header = detect_canonical_header(worksheet)
        working_values = _worksheet_values(worksheet)
    finally:
        workbook.close()
    return _canonicalize_values(
        source_path=inspected.path,
        sheet_name=inspected.sheet_name,
        header=header,
        working_values=working_values,
    )


def _tab_text_workbook(path: Path) -> openpyxl.Workbook | None:
    for encoding in ("utf-8-sig", "gb18030", "gbk", "gb2312"):
        try:
            text = path.read_text(encoding=encoding)
        except UnicodeError:
            continue
        if "\t" not in text:
            return None
        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = "原表"
        for line in text.splitlines():
            worksheet.append([value if value != "" else None for value in line.split("\t")])
        return workbook
    return None


def read_canonical_source(path: str | Path) -> CanonicalWorkbookRead:
    """Dispatch a workbook or Tekla text export into the canonical reader."""
    inspected = inspect_production_input(Path(path))
    if inspected.kind is InputKind.WORKBOOK:
        return read_canonical_workbook(inspected.path)

    workbook = _tab_text_workbook(inspected.path)
    if workbook is None:
        workbook = _space_text_workbook(inspected.path)
    try:
        worksheet = workbook["原表"]
        header = detect_canonical_header(worksheet)
        working_values = _worksheet_values(worksheet)
    finally:
        workbook.close()
    return _canonicalize_values(
        source_path=inspected.path,
        sheet_name="原表",
        header=header,
        working_values=working_values,
    )


def _try_read(input_file: Path, sep: str, enc: str) -> pd.DataFrame | None:
    """Try reading with given separator and encoding. Returns None on failure."""
    try:
        engine = "python" if sep == r"\s+" else "c"
        df = pd.read_csv(
            input_file, sep=sep, encoding=enc, header=None, dtype=str,
            engine=engine,
        )
        if df.shape[1] > 2:
            sample = " ".join(
                str(df.iloc[min(i, len(df) - 1), j])
                for i in range(min(10, len(df)))
                for j in range(min(17, len(df.columns)))
            )
            if any(kw in sample for kw in _CONTENT_KWS):
                return df
    except (UnicodeDecodeError, UnicodeError, pd.errors.ParserError):
        pass
    return None


def _merge_split_headers(headers: list[str]) -> list[str]:
    """Merge adjacent single-CJK-char headers split by whitespace separator.

    E.g. ["型","材","构件名称","材","质"] → ["型材","构件名称","材质"]
    """
    _CJK = _re.compile(r"^[一-鿿]$")
    merged = []
    skip = False
    for i, h in enumerate(headers):
        if skip:
            skip = False
            continue
        if _CJK.match(h) and i + 1 < len(headers) and _CJK.match(headers[i + 1]):
            merged.append(h + headers[i + 1])
            skip = True
        else:
            merged.append(h)
    return merged


def _space_text_workbook(input_file: Path) -> openpyxl.Workbook:
    """Adapt a recognized whitespace-delimited Tekla export to one raw sheet."""
    log.info("Loading %s ...", input_file)

    encodings = ["gbk", "gb2312", "gb18030", "utf-8", "latin-1"]

    # ---- Try tab-separated first (standard Tekla TSV) ----
    raw_df = None
    sep = None
    for enc in encodings:
        raw_df = _try_read(input_file, "\t", enc)
        if raw_df is not None:
            sep = "\t"
            log.info("  Detected: encoding=%s, tab-separated, %d cols.", enc, raw_df.shape[1])
            break

    # ---- Fallback: space-delimited ----
    if raw_df is None:
        for enc in encodings:
            raw_df = _try_read(input_file, r"\s+", enc)
            if raw_df is not None:
                sep = r"\s+"
                log.info("  Detected: encoding=%s, whitespace-separated, %d cols.",
                         enc, raw_df.shape[1])
                break

    # ---- Fallback: real Excel file (.xls/.xlsx binary) ----
    if raw_df is None:
        try:
            raw_df = pd.read_excel(input_file, header=None, dtype=str)
            if raw_df.shape[1] > 2:
                sep = "excel"
                log.info("  Detected: real Excel file, %d cols.", raw_df.shape[1])
        except Exception:
            pass

    if raw_df is None:
        raise ValueError(f"Cannot decode {input_file} with any known encoding/separator.")

    # ---- Detect header row by keyword scoring ----
    keywords = [KW_批次, KW_构件编号, KW_零件号, KW_数量, KW_材质,
                "零件编号", "型材", "型 材", "构件名称"]
    best_row, best_score = 0, 0
    for i in range(min(15, len(raw_df))):
        row_text = " ".join(
            safe_str(raw_df.iloc[i, j]) for j in range(min(17, len(raw_df.columns)))
        )
        score = sum(1 for kw in keywords if kw in row_text)
        if score > best_score:
            best_score = score
            best_row = i
    if best_score < 2:
        raise ValueError("whitespace Tekla text has no credible header candidate")
    header_row = best_row
    log.info("  Detected header at row %d (score=%d/%d).", header_row, best_score, len(keywords))

    # ---- Extract headers ----
    headers = [safe_str(raw_df.iloc[header_row, j]) for j in range(len(raw_df.columns))]
    # Strip unit suffixes like (mm), (kg), (m2)
    headers = [_re.sub(r"\([^)]*\)", "", h).strip() for h in headers]
    # Normalize space-embedded headers: "型 材"→"型材", "材 质"→"材质", "备 注"→"备注"
    headers = [h.replace(" ", "") for h in headers]
    # Merge split single-char CJK headers (whitespace separator artifact)
    headers = _merge_split_headers(headers)

    # ---- Extract data rows ----
    data_rows = []
    for i in range(header_row + 1, len(raw_df)):
        row_vals = [raw_df.iloc[i, j] for j in range(len(raw_df.columns))]
        row_vals = [None if pd.isna(v) else v for v in row_vals]
        data_rows.append(row_vals)

    # ---- Normalize row structure for space-delimited format ----
    # In space-delimited files, col 0 is either 构件编号 (component rows)
    # or 零件编号 (part rows).  Normalize so that:
    #   component rows: col 0=构件编号, col 1=empty(零件号), col 2=型材, ...
    #   part rows:      col 0=empty(构件编号), col 1=零件号, col 2=型材, ...
    import re as _re2
    _PART_NO_RE2 = _re2.compile(
        r"^(\d+[A-Za-z]+-\d+.*|M\d+.*|[a-z]\d+-[a-z]-\d+.*|"
        r"(?!SKG-)[A-Z]{2,5}-\d+.*)$"
    )
    # Material-grade pattern for fallback row-type detection:
    #   starts with Q+3digits (Q345GJB, Q355B, etc.) OR is purely numeric
    #   (bolt grades like "60", non-PL specs like "48").  Must NOT match
    #   strings like "2GL" (构件名称) which start with a digit but contain
    #   letters — those are component names, not materials.
    _MAT_GRADE_RE = _re2.compile(r"^(Q\d{3})")  # Q345, Q355, etc.
    if sep == r"\s+" and len(headers) >= 3:
        norm_data = []
        norm_count = 0
        for row_vals in data_rows:
            c0 = safe_str(row_vals[0]) if len(row_vals) > 0 else ""
            if not c0:
                norm_data.append(row_vals)
                continue

            is_part = bool(_PART_NO_RE2.match(c0.strip()))

            # Fallback: if regex doesn't match, check col 2 for material pattern.
            # In space-delimited files with split-CJK headers (型 材, 材 质),
            # part rows have material (Q345GJB, Q355B) at col 2, component
            # rows have 构件名称 (2GL, albl_Top_f, WGL-, etc.) at col 2.
            if not is_part and len(row_vals) > 2:
                c2 = safe_str(row_vals[2])
                if _MAT_GRADE_RE.match(c2):
                    is_part = True
                elif c2 and c2.replace('.', '', 1).replace('-', '').isdigit():
                    # Pure numeric (or decimal) — bolt grade / non-PL spec
                    # e.g. "48", "0.3", "10.9" but NOT "2GL", "WGL-"
                    is_part = True

            if is_part:
                # Part row: insert empty 构件编号 at front + empty 构件名称 after spec
                # Part rows lack 构件名称 → shift: [part, spec, mat, len, qty, ...]
                # Normalize to:  [None, part, spec, None, mat, len, qty, ...]
                new_row = [None] + row_vals[:2] + [None] + row_vals[2:]
                norm_data.append(new_row)
                norm_count += 1
            else:
                # Component row: insert empty 零件号 at col 1
                new_row = [row_vals[0]] + [None] + row_vals[1:]
                norm_data.append(new_row)
                norm_count += 1
        if norm_count:
            data_rows = norm_data
            # Rename 零件编号→零件号 in header (don't insert duplicate)
            if "零件编号" in headers[1] if len(headers) > 1 else False:
                headers[1] = "零件号"
            log.info("  Normalized row structure: %d rows.", norm_count)

    # ---- Normalize column order to standard layout ----
    # Map variant columns (型材→规格, 单面积→单表面积, etc.) and reorder.
    _STD_KEYS = [
        ("批次","批次"), ("构件编号","构件编号"), ("零件","零件号"),
        ("型材","规格"), ("规格","规格"),
        ("长度","长度"), ("材质","材质"), ("数量","数量"),
        ("单净重","单净重"), ("总净重","总净重"),
        ("单毛重","单毛重"), ("总毛重","总毛重"),
        ("单面","单表面积"), ("总面","总表面积"),
    ]
    col_map = {}
    used_dst = set()
    for src_idx, h in enumerate(headers):
        for kw, dst_name in _STD_KEYS:
            if kw in h and dst_name not in used_dst:
                col_map[src_idx] = (len(col_map), dst_name)
                used_dst.add(dst_name)
                break
    # Only normalize space-delimited files (tab-separated = standard layout)
    if sep == r"\s+" and len(col_map) >= 5 and len(col_map) < len(headers):
        # Reorder to standard Tekla column order:
        # 构件编号, 零件号, 规格, 长度, 材质, 数量, 单净重, 总净重,
        # 单毛重, 总毛重, 单表面积, 总表面积
        _DST_ORDER = [
            "构件编号", "零件号", "规格", "长度", "材质", "数量",
            "单净重", "总净重", "单毛重", "总毛重", "单表面积", "总表面积",
        ]
        # Build lookup: dst_name → src_index
        name_to_src = {dst_name: src for src, (_d, dst_name) in col_map.items()}
        new_headers = []
        src_order = []
        for name in _DST_ORDER:
            if name in name_to_src:
                new_headers.append(name)
                src_order.append(name_to_src[name])
        if len(new_headers) >= 8:
            new_data_rows = []
            for row_vals in data_rows:
                new_data_rows.append([
                    row_vals[src] if src < len(row_vals) else None
                    for src in src_order
                ])
            headers = new_headers
            data_rows = new_data_rows
            log.info("  Normalized column layout: %d cols (Tekla order).", len(headers))

    log.info("  Headers: %s", headers)
    log.info("  Data rows: %d", len(data_rows))

    # ---- Create workbook with 原表 ----
    wb = openpyxl.Workbook()
    ws_raw = wb.active
    ws_raw.title = "原表"

    for j, h in enumerate(headers):
        ws_raw.cell(row=1, column=j + 1, value=h)
    for i, row_vals in enumerate(data_rows):
        for j, val in enumerate(row_vals):
            if val is not None:
                ws_raw.cell(row=i + 2, column=j + 1, value=val)

    return wb
