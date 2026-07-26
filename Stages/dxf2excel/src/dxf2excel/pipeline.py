"""Pipeline orchestrator — connects all modules end-to-end (v2).

New in v2:
- Dynamic column detection (9/10/11 cols)
- Component_no downward fill
- INSERT transform detection
- Adaptive grid tolerances
- Fastener row handling
- Structural warnings (not errors)
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from .assigner import assign_texts_to_cells, merge_cell_texts
from .candidate import detect_drawing_type, identify_table_blocks
from .classifier import classify_rows
from .config import COLUMN_KEYS_9, RowType, WarnCode
from .decoder import decode_all_texts
from .excel_writer import write_excel
from .grid import build_cells, compute_grid_score, estimate_data_columns, recover_grid
from .models import (
    ExtractedRow,
    GridRow,
    TableResult,
    WarningInfo,
)
from .normalizer import normalize_field
from .reader import detect_insert_transforms, read_dxf_blocks
from .validator import validate_table


def process_file(
    filepath: Path,
) -> tuple[list[TableResult], list[WarningInfo]]:
    """Run the full pipeline on a single DXF file (v2)."""
    logger.info(f"Processing: {filepath.name}")

    drawing_type = detect_drawing_type(filepath.name)
    all_warnings: list[WarningInfo] = []
    table_results: list[TableResult] = []

    # 1. Read DXF blocks
    blocks = read_dxf_blocks(filepath)
    if not blocks:
        logger.warning(f"No anonymous blocks found in {filepath.name}")
        all_warnings.append(
            WarningInfo(
                source_file=filepath.name, table_index=-1, row_index=-1,
                warning_code=WarnCode.NO_TABLE_FOUND.value,
                message="No anonymous blocks in DXF",
            )
        )
        return table_results, all_warnings

    # 2. Identify table candidates
    candidates = identify_table_blocks(blocks)
    if not candidates:
        logger.warning(f"No table candidates in {filepath.name}")
        all_warnings.append(
            WarningInfo(
                source_file=filepath.name, table_index=-1, row_index=-1,
                warning_code=WarnCode.NO_TABLE_FOUND.value,
                message="No block passed table candidate scoring",
            )
        )
        return table_results, all_warnings

    best_block, best_stats = candidates[0]
    texts, lines = blocks[best_block]
    logger.info(
        f"  Block: {best_block}  score={best_stats['candidate_score']:.3f}  "
        f"grid_reg={best_stats['grid_regularity']:.3f}  "
        f"TEXT={best_stats['text_count']}  LINE={best_stats['line_count']}"
    )

    # 2b. INSERT transform detection
    insert_info = detect_insert_transforms(filepath, best_block)
    if insert_info["table_insert_transformed"]:
        all_warnings.append(
            WarningInfo(
                source_file=filepath.name, table_index=0, row_index=-1,
                warning_code=WarnCode.INSERT_TRANSFORM.value,
                message=f"Table block INSERT has non-default transform: {insert_info['table_insert_details']}",
            )
        )
    if insert_info["transformed_inserts"] > 0:
        logger.debug(
            f"  INSERTs: {insert_info['transformed_inserts']}/{insert_info['total_inserts']} non-default"
        )

    # 3. Decode \M+5 → GBK, then normalize whitespace/encoding
    decoded_texts = decode_all_texts(texts)
    from .text_normalizer import normalize_text
    for t in decoded_texts:
        t.text = normalize_text(t.text)

    # 4. Recover grid with adaptive tolerances
    y_tol = best_stats.get("y_tolerance")
    x_tol = best_stats.get("x_tolerance")
    row_ys, col_xs, h_lines, v_lines = recover_grid(
        lines, texts,
        y_tolerance=y_tol,
        x_tolerance=x_tol,
    )
    n_rows = len(row_ys) - 1
    n_cols = len(col_xs) - 1
    data_cols = estimate_data_columns(col_xs)

    if n_rows < 1 or n_cols < 1:
        logger.error(f"Grid recovery produced invalid dimensions: {n_rows}×{n_cols}")
        all_warnings.append(
            WarningInfo(
                source_file=filepath.name, table_index=0, row_index=-1,
                warning_code=WarnCode.GRID_IRREGULAR.value,
                message=f"Invalid grid: {n_rows}×{n_cols}",
            )
        )
        return table_results, all_warnings

    logger.info(
        f"  Grid: {n_rows}×{n_cols} ({data_cols} data cols)  "
        f"H={len(h_lines)} V={len(v_lines)}  "
        f"tol_y={y_tol:.2f} tol_x={x_tol:.2f}"
    )

    # 4b. Structural: schema warning if not 9 cols
    if data_cols != 9:
        all_warnings.append(
            WarningInfo(
                source_file=filepath.name, table_index=0, row_index=-1,
                warning_code=WarnCode.SCHEMA_N_COLS.value,
                message=f"Table has {data_cols} data columns (grid: {n_cols} with dividers)",
                raw_value=str(data_cols),
            )
        )

    # 5. Build cells and assign text
    cells = build_cells(row_ys, col_xs)
    cells, orphans = assign_texts_to_cells(cells, decoded_texts)
    merge_cell_texts(cells)

    if orphans > 0:
        logger.warning(f"  Orphan texts: {orphans}/{len(decoded_texts)}")

    # 6. Build GridRow objects
    grid_rows: list[GridRow] = []
    for i, row_cells in enumerate(cells):
        n = len(row_ys)
        y_top = row_ys[n - 1 - i] if i < n - 1 else row_ys[0]
        y_bot = row_ys[n - 2 - i] if i < n - 1 else row_ys[0]
        grid_rows.append(
            GridRow(
                row_index=i,
                y_min=min(y_bot, y_top),
                y_max=max(y_bot, y_top),
                cells=row_cells,
            )
        )

    # 7. Classify rows (v2: component_summary, fastener_data, downward fill)
    grid_rows = classify_rows(grid_rows)

    # 8. Select column keys based on detected schema
    # Map col index → field key, handling divider columns
    col_keys = _build_column_key_map(n_cols, data_cols, grid_rows)

    # 8b. Extract data rows + downward fill component_no
    data_rows: list[ExtractedRow] = []
    current_component_no: str | None = None

    for gr in grid_rows:
        cell_texts = [c.merged_text for c in gr.cells]

        # Track component_no from component_summary rows
        if gr.row_type == RowType.COMPONENT_SUMMARY:
            comp_no = cell_texts[0].strip() if cell_texts else ""
            if comp_no:
                current_component_no = comp_no

        # Skip non-data rows in all_rows output
        if gr.row_type not in (RowType.DATA, RowType.FASTENER_DATA, RowType.COMPONENT_SUMMARY):
            continue

        # Normalize fields
        row_dict: dict[str, object] = {}
        row_confidences: list[float] = []

        for j, key in col_keys.items():
            raw = cell_texts[j] if j < len(cell_texts) else ""
            val, conf = normalize_field(raw, key)
            row_dict[key] = val
            row_confidences.append(conf)

        avg_conf = sum(row_confidences) / max(len(row_confidences), 1)

        # Downward fill component_no
        comp_no = row_dict.get("component_no")
        comp_no_str = str(comp_no) if comp_no and str(comp_no).strip() else None
        if not comp_no_str and current_component_no:
            comp_no_str = current_component_no

        # Determine row_subtype
        if gr.row_type == RowType.COMPONENT_SUMMARY:
            subtype = "component_summary"
        elif gr.row_type == RowType.FASTENER_DATA:
            subtype = "fastener_data"
        else:
            subtype = "data"

        extracted = ExtractedRow(
            source_file=filepath.name,
            drawing_type=drawing_type,
            row_index=gr.row_index,
            row_subtype=subtype,
            component_no=comp_no_str,
            part_no=_str_or_none(row_dict.get("part_no")),
            spec=_str_or_none(row_dict.get("spec")),
            length_mm=_float_or_none(row_dict.get("length_mm")),
            material=_str_or_none(row_dict.get("material")),
            quantity=_int_or_none(row_dict.get("quantity")),
            unit_weight_kg=_float_or_none(row_dict.get("unit_weight_kg")),
            total_weight_kg=_float_or_none(row_dict.get("total_weight_kg")),
            area_m2=_float_or_none(row_dict.get("area_m2")),
            remark=_str_or_none(row_dict.get("remark")) or "",
            confidence=round(avg_conf, 4),
            raw_cells=cell_texts,
        )
        data_rows.append(extracted)

    # 9. Build TableResult
    gs = compute_grid_score(cells, row_ys, col_xs)
    total_cells = sum(len(row) for row in cells)
    non_empty_cells = sum(1 for row in cells for c in row if c.merged_text)
    fill_rate = non_empty_cells / max(total_cells, 1)

    table = TableResult(
        source_file=filepath.name,
        drawing_type=drawing_type,
        source_block=best_block,
        bbox_x1=best_stats["bbox_x1"],
        bbox_y1=best_stats["bbox_y1"],
        bbox_x2=best_stats["bbox_x2"],
        bbox_y2=best_stats["bbox_y2"],
        num_rows=n_rows,
        num_cols=n_cols,
        data_cols=data_cols,
        data_rows=data_rows,
        grid_rows=grid_rows,
        text_count=best_stats["text_count"],
        line_count=best_stats["line_count"],
        candidate_score=best_stats["candidate_score"],
        fill_rate=round(fill_rate, 4),
        grid_score=gs,
        grid_regularity=best_stats["grid_regularity"],
    )

    # 10. Validate (v2: relaxed rules)
    validation_warnings = validate_table(table)
    all_warnings.extend(validation_warnings)

    table_results.append(table)
    logger.info(
        f"  → {len(data_rows)} data rows extracted, "
        f"{len(validation_warnings)} validation warnings"
    )

    return table_results, all_warnings


def _build_column_key_map(
    n_cols: int,
    data_cols: int,
    grid_rows: list[GridRow],
) -> dict[int, str]:
    """Build col_index → field_key mapping.

    For 9-col (B7): maps directly to COLUMN_KEYS_9.
    For 10-col (SKG): col 0 → component_no, rest → COLUMN_KEYS_9.
    For 11-col (SKG+divider): col 0 → component_no, skip divider col, rest → COLUMN_KEYS_9.
    """
    # Default: use header row to detect which columns map to which fields
    header_row = None
    for gr in grid_rows:
        if gr.row_type == RowType.HEADER:
            header_row = gr
            break

    if header_row is None:
        # No header — fall back to position-based mapping
        if n_cols <= 9:
            return {j: COLUMN_KEYS_9[j] for j in range(min(n_cols, 9))}
        elif n_cols == 10:
            return _build_10col_map()
        else:
            return _build_11col_map()

    # Use header cell text to determine field mapping
    from .text_normalizer import header_to_field_key

    mapping: dict[int, str] = {}
    col_keys_used: set[str] = set()

    for j, cell in enumerate(header_row.cells):
        text = cell.merged_text
        if not text:
            continue
        field_key = header_to_field_key(text)
        if field_key and field_key not in col_keys_used:
            mapping[j] = field_key
            col_keys_used.add(field_key)

    # Fill gaps with position-based fallback
    if not mapping:
        if n_cols <= 9:
            mapping = {j: COLUMN_KEYS_9[j] for j in range(min(n_cols, 9))}
        elif n_cols == 10:
            mapping = _build_10col_map()
        else:
            mapping = _build_11col_map()

    return mapping


def _build_10col_map() -> dict[int, str]:
    """10-col: component_no + 9 standard."""
    m = {0: "component_no"}
    for j, key in enumerate(COLUMN_KEYS_9):
        m[j + 1] = key
    return m


def _build_11col_map() -> dict[int, str]:
    """11-col: component_no + 9 standard + 1 divider (skip col 6)."""
    m = {0: "component_no"}
    # cols 1-5 → part_no, spec, length, material, quantity
    for j, key in enumerate(COLUMN_KEYS_9[:5]):
        m[j + 1] = key
    # col 6 = divider (skip)
    # cols 7-10 → unit_weight, total_weight, area, remark
    for j, key in enumerate(COLUMN_KEYS_9[5:]):
        m[j + 7] = key
    return m


# ---- Type helpers ----

def _str_or_none(val: object) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _float_or_none(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _int_or_none(val: object) -> int | None:
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float) and val == int(val):
        return int(val)
    return None


def process_all(
    input_dir: Path,
    output_path: Path,
) -> None:
    """Process all .dxf files in input_dir, write combined Excel."""
    dxf_files = sorted(input_dir.glob("*.dxf"))
    dxf_files = [f for f in dxf_files if not f.name.startswith(".")]

    if not dxf_files:
        logger.error(f"No .dxf files found in {input_dir}")
        return

    logger.info(f"Found {len(dxf_files)} DXF files")

    all_tables: list[TableResult] = []
    all_warnings: list[WarningInfo] = []
    success = 0
    failed = 0

    for fp in dxf_files:
        try:
            tables, warnings = process_file(fp)
            all_tables.extend(tables)
            all_warnings.extend(warnings)
            if tables:
                success += 1
            else:
                failed += 1
        except Exception as exc:
            logger.error(f"Failed to process {fp.name}: {exc}")
            all_warnings.append(
                WarningInfo(
                    source_file=fp.name, table_index=-1, row_index=-1,
                    warning_code=WarnCode.PROCESSING_ERROR.value,
                    message=str(exc),
                )
            )
            failed += 1

    total_data_rows = sum(len(t.data_rows) for t in all_tables)
    logger.info(
        f"Done: {success} succeeded, {failed} failed, "
        f"{len(all_tables)} tables, {total_data_rows} data rows, "
        f"{len(all_warnings)} warnings"
    )

    write_excel(output_path, all_tables, all_warnings)
    logger.info(f"Output written to {output_path}")
