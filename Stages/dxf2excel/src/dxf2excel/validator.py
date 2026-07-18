"""Quality validation rules for extracted tables.

v2 changes:
- Relaxed part_no requirement for fastener rows.
- New structural/branch warnings instead of errors.
- Component merge detection.
- Large table detection.
"""

from __future__ import annotations

from .config import MIN_FILL_RATE, WEIGHT_TOLERANCE_RATIO, WarnCode
from .models import ExtractedRow, TableResult, WarningInfo


def validate_table(table: TableResult) -> list[WarningInfo]:
    """Run quality checks on a parsed table."""
    warnings: list[WarningInfo] = []

    # 1. Structural / branch indicators (not errors)
    _check_schema(table, warnings)
    _check_large_table(table, warnings)

    # 2. Fill rate
    _check_fill_rate(table, warnings)

    # 3. Header
    _check_header(table, warnings)

    # 4. Per-row validation
    for row in table.data_rows:
        _check_weight_consistency(row, warnings)
        _check_required_fields(row, warnings)
        _check_ranges(row, warnings)

    # 5. Duplicates
    _check_duplicates(table, warnings)

    # 6. Total row
    _check_total_row(table, warnings)

    return warnings


def _check_schema(table: TableResult, warnings: list[WarningInfo]) -> None:
    """Structural indicator: non-9-column table detected."""
    if table.data_cols != 9:
        warnings.append(
            WarningInfo(
                source_file=table.source_file,
                table_index=0,
                row_index=-1,
                warning_code=WarnCode.SCHEMA_N_COLS.value,
                message=f"Table has {table.data_cols} data columns (not standard 9)",
                raw_value=str(table.data_cols),
            )
        )


def _check_large_table(table: TableResult, warnings: list[WarningInfo]) -> None:
    """Structural indicator: large table (>500 entities)."""
    if table.text_count + table.line_count > 500:
        warnings.append(
            WarningInfo(
                source_file=table.source_file,
                table_index=0,
                row_index=-1,
                warning_code=WarnCode.LARGE_TABLE.value,
                message=f"Large table: {table.text_count + table.line_count} entities "
                f"(TEXT={table.text_count}, LINE={table.line_count})",
            )
        )


def _check_fill_rate(table: TableResult, warnings: list[WarningInfo]) -> None:
    if table.fill_rate < MIN_FILL_RATE:
        warnings.append(
            WarningInfo(
                source_file=table.source_file,
                table_index=0,
                row_index=-1,
                warning_code=WarnCode.LOW_FILL.value,
                message=f"Fill rate {table.fill_rate:.1%} below minimum {MIN_FILL_RATE:.0%}",
            )
        )


def _check_header(table: TableResult, warnings: list[WarningInfo]) -> None:
    has_header = any(
        row.row_type.value in ("header", "subheader") for row in table.grid_rows
    )
    if not has_header:
        warnings.append(
            WarningInfo(
                source_file=table.source_file,
                table_index=0,
                row_index=-1,
                warning_code=WarnCode.HEADER_MISMATCH.value,
                message="No header/subheader row detected",
            )
        )


def _check_weight_consistency(
    row: ExtractedRow, warnings: list[WarningInfo]
) -> None:
    """Weight check — skipped for fastener and component_summary rows."""
    if row.row_subtype in ("fastener_data", "component_summary"):
        return

    if (
        row.quantity is not None
        and row.unit_weight_kg is not None
        and row.total_weight_kg is not None
        and row.total_weight_kg > 0
    ):
        expected = row.quantity * row.unit_weight_kg
        actual = row.total_weight_kg
        deviation = abs(expected - actual) / actual
        if deviation > WEIGHT_TOLERANCE_RATIO:
            warnings.append(
                WarningInfo(
                    source_file=row.source_file,
                    table_index=0,
                    row_index=row.row_index,
                    warning_code=WarnCode.WEIGHT_MISMATCH.value,
                    message=(
                        f"{row.quantity} × {row.unit_weight_kg} = {expected:.2f} "
                        f"vs total={actual:.2f} (deviation {deviation:.1%})"
                    ),
                    raw_value=str(actual),
                )
            )


def _check_required_fields(
    row: ExtractedRow, warnings: list[WarningInfo]
) -> None:
    """Check required fields.

    Relaxed for fastener rows: part_no can be empty.
    Relaxed for fastener rows: unit_weight_kg / total_weight_kg / area_m2 can be empty.
    """
    is_fastener = row.row_subtype == "fastener_data"
    is_component = row.row_subtype == "component_summary"

    # For component_summary rows, skip all field checks (they're aggregation rows)
    if is_component:
        return

    # Required for all data rows: spec, material, quantity
    for field, label in [
        ("spec", "截面型材"),
        ("material", "材质"),
    ]:
        val = getattr(row, field, None)
        if val is None or (isinstance(val, str) and not val.strip()):
            warnings.append(
                WarningInfo(
                    source_file=row.source_file,
                    table_index=0,
                    row_index=row.row_index,
                    warning_code=WarnCode.EMPTY_REQUIRED.value,
                    message=f"Required field '{label}' is empty",
                )
            )

    if row.quantity is None:
        warnings.append(
            WarningInfo(
                source_file=row.source_file,
                table_index=0,
                row_index=row.row_index,
                warning_code=WarnCode.EMPTY_REQUIRED.value,
                message="Required field '数量' is empty",
            )
        )

    # part_no is required only for non-fastener rows
    if not is_fastener:
        if row.part_no is None or (
            isinstance(row.part_no, str) and not row.part_no.strip()
        ):
            warnings.append(
                WarningInfo(
                    source_file=row.source_file,
                    table_index=0,
                    row_index=row.row_index,
                    warning_code=WarnCode.EMPTY_REQUIRED.value,
                    message="Required field '零件号' is empty",
                )
            )
    else:
        # Fastener row: log as structural indicator, not error
        if row.part_no is None or (
            isinstance(row.part_no, str) and not row.part_no.strip()
        ):
            warnings.append(
                WarningInfo(
                    source_file=row.source_file,
                    table_index=0,
                    row_index=row.row_index,
                    warning_code=WarnCode.FASTENER_ROW.value,
                    message="Fastener row: part_no empty (expected for bolts/studs)",
                    raw_value=str(row.spec or ""),
                )
            )


def _check_ranges(row: ExtractedRow, warnings: list[WarningInfo]) -> None:
    if row.length_mm is not None:
        if row.length_mm > 20000:
            warnings.append(
                WarningInfo(
                    source_file=row.source_file,
                    table_index=0,
                    row_index=row.row_index,
                    warning_code=WarnCode.LENGTH_RANGE.value,
                    message=f"Length {row.length_mm}mm > 20000mm",
                    raw_value=str(row.length_mm),
                )
            )
        if row.length_mm is not None and row.length_mm < 1:
            warnings.append(
                WarningInfo(
                    source_file=row.source_file,
                    table_index=0,
                    row_index=row.row_index,
                    warning_code=WarnCode.LENGTH_RANGE.value,
                    message=f"Length {row.length_mm}mm < 1mm",
                    raw_value=str(row.length_mm),
                )
            )

    if row.quantity is not None and row.quantity > 1000:
        warnings.append(
            WarningInfo(
                source_file=row.source_file,
                table_index=0,
                row_index=row.row_index,
                warning_code=WarnCode.QUANTITY_RANGE.value,
                message=f"Quantity {row.quantity} > 1000",
                raw_value=str(row.quantity),
            )
        )


def _check_duplicates(table: TableResult, warnings: list[WarningInfo]) -> None:
    seen: dict[str, int] = {}
    for row in table.data_rows:
        if row.part_no:
            pn = row.part_no.strip().lower()
            if pn in seen:
                warnings.append(
                    WarningInfo(
                        source_file=table.source_file,
                        table_index=0,
                        row_index=row.row_index,
                        warning_code=WarnCode.DUPLICATE_PART.value,
                        message=(
                            f"Duplicate part_no '{row.part_no}' "
                            f"(also at row {seen[pn]})"
                        ),
                        raw_value=row.part_no,
                    )
                )
            else:
                seen[pn] = row.row_index


def _check_total_row(table: TableResult, warnings: list[WarningInfo]) -> None:
    has_total = any(
        row.row_type.value in ("total", "summary") for row in table.grid_rows
    )
    if not has_total:
        warnings.append(
            WarningInfo(
                source_file=table.source_file,
                table_index=0,
                row_index=-1,
                warning_code=WarnCode.NO_TOTAL_ROW.value,
                message="No total/summary row detected",
            )
        )
