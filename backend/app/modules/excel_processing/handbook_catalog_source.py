"""Authoritative Excel source model for the hardware-handbook database.

The workbook is the import authority.  Every semantic database record keeps a
one-to-one foreign key to one non-empty workbook row.  Duplicate rows are
preserved; lookup code is responsible for rejecting conflicting weights.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

import xlrd

AUTHORITATIVE_SOURCE_SHA256 = (
    "391ee19724fe6db5b6195640c2aab26ff1daf38a9b1660193846ebce504271bb"
)


@dataclass(frozen=True, slots=True)
class SourceRow:
    source_row_id: int
    sheet_index: int
    sheet_name: str
    row_number: int
    record_type: str
    raw_values: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class SemanticRecord:
    source_row_id: int
    sheet_index: int
    sheet_name: str
    row_number: int
    values: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class HandbookSnapshot:
    source_path: Path
    sha256: str
    file_size: int
    sheet_count: int
    source_rows: tuple[SourceRow, ...]
    table_records: Mapping[str, tuple[SemanticRecord, ...]]
    lookup_conflicts: Mapping[str, Mapping[str, tuple[Decimal, ...]]]

    @property
    def semantic_record_count(self) -> int:
        return sum(len(records) for records in self.table_records.values())


@dataclass(frozen=True, slots=True)
class DatabaseContent:
    """Exact rows read from one deployed handbook database."""

    manifest: Mapping[str, int]
    table_rows: Mapping[str, tuple[tuple[object, ...], ...]]


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    number = Decimal(str(value))
    if number.as_tuple().exponent < -9:
        # xlrd exposes Excel's IEEE-754 storage tails.  Nine decimal places
        # retain every meaningful source value in this reviewed workbook and
        # match the exact DECIMAL representation used by the database.
        number = number.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    return number


def _text(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _number_text(value: object) -> str:
    number = _decimal(value)
    if number is None:
        return ""
    rendered = format(number, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _w_lookup_key(value: str | None) -> str | None:
    if value is None:
        return None
    compact = value.replace(" ", "").replace("　", "").upper()
    return re.sub(r"(?<=\d)[X×](?=\d)", "*", compact)


def _nonempty(values: Iterable[object]) -> bool:
    return any(value not in (None, "") for value in values)


class _SnapshotBuilder:
    def __init__(self, book: xlrd.book.Book) -> None:
        self.book = book
        self.source_row_ids: dict[tuple[int, int], int] = {}
        self.source_values: dict[tuple[int, int], tuple[object, ...]] = {}
        self.record_types: dict[tuple[int, int], str] = {}
        self.tables: dict[str, list[SemanticRecord]] = defaultdict(list)
        next_id = 1
        for sheet_index, _sheet_name in enumerate(book.sheet_names()):
            sheet = book.sheet_by_index(sheet_index)
            for row_index in range(sheet.nrows):
                values = tuple(sheet.row_values(row_index))
                if not _nonempty(values):
                    continue
                coordinate = (sheet_index, row_index + 1)
                self.source_row_ids[coordinate] = next_id
                self.source_values[coordinate] = values
                next_id += 1

    def add(
        self,
        table: str,
        sheet_name: str,
        row_number: int,
        **values: object,
    ) -> None:
        sheet_index = self.book.sheet_names().index(sheet_name)
        coordinate = (sheet_index, row_number)
        if coordinate in self.record_types:
            raise ValueError(
                f"{sheet_name}!{row_number} maps to more than one semantic record"
            )
        source_row_id = self.source_row_ids.get(coordinate)
        if source_row_id is None:
            raise ValueError(f"{sheet_name}!{row_number} is not a non-empty source row")
        self.record_types[coordinate] = table
        self.tables[table].append(
            SemanticRecord(
                source_row_id=source_row_id,
                sheet_index=sheet_index,
                sheet_name=sheet_name,
                row_number=row_number,
                values=MappingProxyType(dict(values)),
            )
        )

    def data_rows(self, sheet_name: str, start_row: int) -> Iterable[tuple[int, list[Any]]]:
        sheet = self.book.sheet_by_name(sheet_name)
        for row_number in range(start_row, sheet.nrows + 1):
            values = sheet.row_values(row_number - 1)
            if _nonempty(values):
                yield row_number, values

    def finish(
        self,
    ) -> tuple[
        tuple[SourceRow, ...],
        Mapping[str, tuple[SemanticRecord, ...]],
    ]:
        rows = []
        for (sheet_index, row_number), source_row_id in sorted(
            self.source_row_ids.items(),
            key=lambda item: item[1],
        ):
            rows.append(
                SourceRow(
                    source_row_id=source_row_id,
                    sheet_index=sheet_index,
                    sheet_name=self.book.sheet_names()[sheet_index],
                    row_number=row_number,
                    record_type=self.record_types.get(
                        (sheet_index, row_number),
                        "unmapped",
                    ),
                    raw_values=self.source_values[(sheet_index, row_number)],
                )
            )
        tables = {
            table: tuple(records)
            for table, records in sorted(self.tables.items())
        }
        return tuple(rows), MappingProxyType(tables)


def _add_pipe_convert(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("钢管转换", 5):
        builder.add(
            "pipe_convert",
            "钢管转换",
            row,
            nominal_diameter=_text(values[2]),
            pipe_spec=_text(values[3]),
            pipe_weight=_decimal(values[4]),
            rebar_weight=_decimal(values[5]),
            lookup_enabled=True,
        )


def _add_checkered_plate(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("花纹板", 4):
        builder.add(
            "checkered_plate",
            "花纹板",
            row,
            thickness=_decimal(values[1]),
            diamond_weight=_decimal(values[2]),
            lentil_weight=_decimal(values[3]),
            yb200x_weight=_decimal(values[4]),
            round_bean_weight=_decimal(values[5]),
            lookup_enabled=True,
        )


def _add_stainless_steel(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("不锈钢", 3):
        builder.add(
            "stainless_steel",
            "不锈钢",
            row,
            product_name=_text(values[1]),
            material_grade=_text(values[2]),
            density=_decimal(values[3]),
            lookup_enabled=True,
        )


def _add_h_beam(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("H型钢", 3):
        spec = _text(values[1])
        builder.add(
            "h_beam",
            "H型钢",
            row,
            source_spec=spec,
            lookup_spec=spec,
            weight_98=_decimal(values[2]),
            weight_2005=_decimal(values[3]),
            weight_2010=_decimal(values[4]),
            lookup_enabled=True,
        )
    for sheet_name, start_row, spec_column, weight_column in (
        ("H型钢 (2)", 2, 1, 2),
        ("1", 2, 0, 1),
    ):
        for row, values in builder.data_rows(sheet_name, start_row):
            source_spec = _text(values[spec_column])
            builder.add(
                "h_beam",
                sheet_name,
                row,
                source_spec=source_spec,
                lookup_spec=f"H{source_spec}",
                weight_98=None,
                weight_2005=None,
                weight_2010=_decimal(values[weight_column]),
                lookup_enabled=False,
            )


def _add_w_and_embedded_hfw(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("W型钢", 3):
        us_spec1 = _text(values[2])
        us_spec2 = _text(values[3])
        if us_spec1 and us_spec1.upper().startswith("W") and us_spec2 and us_spec2.upper().startswith("X"):
            builder.add(
                "w_beam",
                "W型钢",
                row,
                us_spec1=us_spec1,
                us_spec2=us_spec2,
                cn_spec=_text(values[4]),
                lookup_us_spec=_w_lookup_key(f"{us_spec1}{us_spec2}"),
                lookup_cn_spec=_w_lookup_key(_text(values[4])),
                cross_section_area=_decimal(values[5]),
                height=_decimal(values[6]),
                flange_width=_decimal(values[7]),
                web_thickness=_decimal(values[8]),
                flange_thickness=_decimal(values[9]),
                weight=_decimal(values[10]),
                lookup_enabled=True,
            )
            continue
        height = _decimal(values[6])
        width = _decimal(values[7])
        web_t = _decimal(values[8])
        flange_t = _decimal(values[9])
        source_spec = us_spec1
        lookup_spec = source_spec or (
            f"H{_number_text(height)}*{_number_text(width)}*"
            f"{_number_text(web_t)}*{_number_text(flange_t)}"
        )
        builder.add(
            "hfw_pipe",
            "W型钢",
            row,
            profile_family="W型钢内嵌高频焊",
            source_spec=source_spec,
            lookup_spec=lookup_spec,
            height=height,
            width=width,
            web_thickness=web_t,
            flange_thickness=flange_t,
            weight=_decimal(values[10]),
            lookup_enabled=True,
        )


def _add_square_tube(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("方管", 4):
        spec = _text(values[2])
        builder.add(
            "square_tube",
            "方管",
            row,
            tube_type=_text(values[1]),
            source_spec=spec,
            lookup_spec=spec,
            side_a=_decimal(values[3]),
            side_b=_decimal(values[4]),
            wall_thickness=_decimal(values[5]),
            reference_length=_decimal(values[6]),
            weight=_decimal(values[7]),
            lookup_enabled=True,
        )


def _add_channel(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("槽钢", 3):
        spec = _text(values[2])
        builder.add(
            "channel",
            "槽钢",
            row,
            channel_name=_text(values[1]),
            source_spec=spec,
            lookup_spec=spec,
            weight=_decimal(values[3]),
            lookup_enabled=True,
        )
    for row, values in builder.data_rows("槽钢 (2)", 2):
        source_spec = _text(values[2])
        builder.add(
            "channel",
            "槽钢 (2)",
            row,
            channel_name=_text(values[1]),
            source_spec=source_spec,
            lookup_spec=f"[{source_spec}",
            weight=_decimal(values[3]),
            lookup_enabled=False,
        )


def _add_round_square_bar(builder: _SnapshotBuilder) -> None:
    for sheet_name, enabled in (("圆钢、方钢 (2)", True), ("圆钢、方钢", False)):
        for row, values in builder.data_rows(sheet_name, 5):
            builder.add(
                "round_square_bar",
                sheet_name,
                row,
                diameter_or_side=_decimal(values[1]),
                round_weight=_decimal(values[2]),
                square_weight=_decimal(values[3]),
                lookup_enabled=enabled,
            )


def _add_angle(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("角钢", 3):
        spec = _text(values[2])
        builder.add(
            "angle",
            "角钢",
            row,
            angle_type=_text(values[1]),
            source_spec=spec,
            lookup_spec=spec,
            weight=_decimal(values[3]),
            lookup_enabled=True,
        )
    for row, values in builder.data_rows("角钢 (2)", 2):
        source_spec = _text(values[2])
        builder.add(
            "angle",
            "角钢 (2)",
            row,
            angle_type=_text(values[1]),
            source_spec=source_spec,
            lookup_spec=f"L{source_spec}",
            weight=_decimal(values[3]),
            lookup_enabled=False,
        )


def _add_i_beam(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("工字钢", 3):
        spec = _text(values[2])
        builder.add(
            "i_beam",
            "工字钢",
            row,
            beam_name=_text(values[1]),
            source_spec=spec,
            lookup_spec=spec,
            weight=_decimal(values[3]),
            lookup_enabled=True,
        )
    for row, values in builder.data_rows("工字钢 (2)", 2):
        source_spec = _text(values[2])
        builder.add(
            "i_beam",
            "工字钢 (2)",
            row,
            beam_name=_text(values[1]),
            source_spec=source_spec,
            lookup_spec=f"I{source_spec}",
            weight=_decimal(values[3]),
            lookup_enabled=False,
        )


def _add_steel_pipe(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("钢管", 3):
        if not _text(values[1]):
            continue
        spec = _text(values[1])
        builder.add(
            "steel_pipe",
            "钢管",
            row,
            source_spec=spec,
            lookup_spec=spec,
            weight=_decimal(values[2]),
            lookup_enabled=True,
        )


def _add_h_beam_us(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("H型钢 (美标)", 3):
        if not _text(values[1]):
            continue
        spec = _text(values[1])
        builder.add(
            "h_beam_us",
            "H型钢 (美标)",
            row,
            source_spec=spec,
            lookup_spec=spec,
            weight=_decimal(values[2]),
            weight_per_12m=_decimal(values[3]),
            lookup_enabled=True,
        )


def _add_u_channel_us(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("U型槽钢 (美标)", 3):
        spec = _text(values[2])
        builder.add(
            "u_channel_us",
            "U型槽钢 (美标)",
            row,
            channel_name=_text(values[1]),
            source_spec=spec,
            lookup_spec=spec,
            weight=_decimal(values[3]),
            lookup_enabled=True,
        )


def _add_angle_us(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("角钢 (美标)", 3):
        spec = _text(values[2])
        builder.add(
            "angle_us",
            "角钢 (美标)",
            row,
            angle_type=_text(values[1]),
            source_spec=spec,
            lookup_spec=spec,
            weight=_decimal(values[3]),
            lookup_enabled=True,
        )


def _add_flat_steel(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("扁钢", 3):
        source_spec = _text(values[1])
        builder.add(
            "flat_steel",
            "扁钢",
            row,
            source_spec=source_spec,
            lookup_spec=source_spec.lstrip("_") if source_spec else None,
            weight=_decimal(values[2]),
            lookup_enabled=True,
        )


def _add_t_beam(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("T型钢", 3):
        spec = _text(values[1])
        builder.add(
            "t_beam",
            "T型钢",
            row,
            source_spec=spec,
            lookup_spec=spec,
            weight_98=_decimal(values[2]),
            weight_2005=_decimal(values[3]),
            weight_2010=_decimal(values[4]),
            h_beam_series=_text(values[5]),
            lookup_enabled=True,
        )


def _add_high_rise_thickness(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("高建钢理论厚度", 5):
        builder.add(
            "high_rise_steel_thickness",
            "高建钢理论厚度",
            row,
            nominal_thickness=_decimal(values[0]),
            converted_le_1500=_decimal(values[1]),
            converted_gt_1500_le_2500=_decimal(values[2]),
            converted_gt_2500_le_4000=_decimal(values[3]),
            lookup_enabled=True,
        )


def _add_rebar(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("螺纹钢", 4):
        builder.add(
            "rebar",
            "螺纹钢",
            row,
            nominal_diameter=_decimal(values[3]),
            weight=_decimal(values[4]),
            lookup_enabled=True,
        )


def _add_reducer(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("大小头", 1):
        builder.add(
            "reducer",
            "大小头",
            row,
            source_value=_text(values[0]),
            lookup_enabled=False,
        )


def _add_hfw_pipe(builder: _SnapshotBuilder) -> None:
    for row, values in builder.data_rows("高频焊", 2):
        spec = _text(values[4])
        builder.add(
            "hfw_pipe",
            "高频焊",
            row,
            profile_family="高频焊",
            source_spec=spec,
            lookup_spec=spec,
            height=_decimal(values[0]),
            width=_decimal(values[1]),
            web_thickness=_decimal(values[2]),
            flange_thickness=_decimal(values[3]),
            weight=_decimal(values[5]),
            lookup_enabled=True,
        )


def _lookup_conflicts(
    tables: Mapping[str, tuple[SemanticRecord, ...]],
) -> Mapping[str, Mapping[str, tuple[Decimal, ...]]]:
    result: dict[str, Mapping[str, tuple[Decimal, ...]]] = {}
    for table, records in tables.items():
        weights: dict[str, set[Decimal]] = defaultdict(set)
        for record in records:
            if not record.values.get("lookup_enabled"):
                continue
            spec = record.values.get("lookup_spec")
            weight = record.values.get("weight")
            if isinstance(spec, str) and isinstance(weight, Decimal):
                weights[spec].add(weight)
        conflicts = {
            spec: tuple(sorted(values))
            for spec, values in weights.items()
            if len(values) > 1
        }
        if conflicts:
            result[table] = MappingProxyType(conflicts)
    return MappingProxyType(result)


def load_handbook_workbook(path: str | Path) -> HandbookSnapshot:
    source_path = Path(path).resolve()
    sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if sha256 != AUTHORITATIVE_SOURCE_SHA256:
        raise ValueError(
            "hardware-handbook source hash does not match the reviewed authority"
        )
    book = xlrd.open_workbook(source_path, formatting_info=True, on_demand=True)
    try:
        builder = _SnapshotBuilder(book)
        for add_records in (
            _add_pipe_convert,
            _add_checkered_plate,
            _add_stainless_steel,
            _add_h_beam,
            _add_w_and_embedded_hfw,
            _add_square_tube,
            _add_channel,
            _add_round_square_bar,
            _add_angle,
            _add_i_beam,
            _add_steel_pipe,
            _add_h_beam_us,
            _add_u_channel_us,
            _add_angle_us,
            _add_flat_steel,
            _add_t_beam,
            _add_high_rise_thickness,
            _add_rebar,
            _add_reducer,
            _add_hfw_pipe,
        ):
            add_records(builder)
        source_rows, tables = builder.finish()
        return HandbookSnapshot(
            source_path=source_path,
            sha256=sha256,
            file_size=source_path.stat().st_size,
            sheet_count=book.nsheets,
            source_rows=source_rows,
            table_records=tables,
            lookup_conflicts=_lookup_conflicts(tables),
        )
    finally:
        book.release_resources()


_TABLE_COLUMNS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "angle": (
        ("angle_type", "varchar(30)"),
        ("source_spec", "varchar(50)"),
        ("lookup_spec", "varchar(50)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "angle_us": (
        ("angle_type", "varchar(30)"),
        ("source_spec", "varchar(50)"),
        ("lookup_spec", "varchar(50)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "channel": (
        ("channel_name", "varchar(30)"),
        ("source_spec", "varchar(50)"),
        ("lookup_spec", "varchar(50)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "checkered_plate": (
        ("thickness", "decimal(18,9)"),
        ("diamond_weight", "decimal(18,9)"),
        ("lentil_weight", "decimal(18,9)"),
        ("yb200x_weight", "decimal(18,9)"),
        ("round_bean_weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "flat_steel": (
        ("source_spec", "varchar(50)"),
        ("lookup_spec", "varchar(50)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "h_beam": (
        ("source_spec", "varchar(50)"),
        ("lookup_spec", "varchar(50)"),
        ("weight_98", "decimal(18,9)"),
        ("weight_2005", "decimal(18,9)"),
        ("weight_2010", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "h_beam_us": (
        ("source_spec", "varchar(50)"),
        ("lookup_spec", "varchar(50)"),
        ("weight", "decimal(18,9)"),
        ("weight_per_12m", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "hfw_pipe": (
        ("profile_family", "varchar(40)"),
        ("source_spec", "varchar(60)"),
        ("lookup_spec", "varchar(60)"),
        ("height", "decimal(18,9)"),
        ("width", "decimal(18,9)"),
        ("web_thickness", "decimal(18,9)"),
        ("flange_thickness", "decimal(18,9)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "high_rise_steel_thickness": (
        ("nominal_thickness", "decimal(18,9)"),
        ("converted_le_1500", "decimal(18,9)"),
        ("converted_gt_1500_le_2500", "decimal(18,9)"),
        ("converted_gt_2500_le_4000", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "i_beam": (
        ("beam_name", "varchar(30)"),
        ("source_spec", "varchar(50)"),
        ("lookup_spec", "varchar(50)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "pipe_convert": (
        ("nominal_diameter", "varchar(30)"),
        ("pipe_spec", "varchar(50)"),
        ("pipe_weight", "decimal(18,9)"),
        ("rebar_weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "rebar": (
        ("nominal_diameter", "decimal(18,9)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "reducer": (
        ("source_value", "varchar(255)"),
        ("lookup_enabled", "boolean"),
    ),
    "round_square_bar": (
        ("diameter_or_side", "decimal(18,9)"),
        ("round_weight", "decimal(18,9)"),
        ("square_weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "square_tube": (
        ("tube_type", "varchar(30)"),
        ("source_spec", "varchar(60)"),
        ("lookup_spec", "varchar(60)"),
        ("side_a", "decimal(18,9)"),
        ("side_b", "decimal(18,9)"),
        ("wall_thickness", "decimal(18,9)"),
        ("reference_length", "decimal(18,9)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "stainless_steel": (
        ("product_name", "varchar(50)"),
        ("material_grade", "varchar(50)"),
        ("density", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "steel_pipe": (
        ("source_spec", "varchar(60)"),
        ("lookup_spec", "varchar(60)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "t_beam": (
        ("source_spec", "varchar(60)"),
        ("lookup_spec", "varchar(60)"),
        ("weight_98", "decimal(18,9)"),
        ("weight_2005", "decimal(18,9)"),
        ("weight_2010", "decimal(18,9)"),
        ("h_beam_series", "varchar(40)"),
        ("lookup_enabled", "boolean"),
    ),
    "u_channel_us": (
        ("channel_name", "varchar(30)"),
        ("source_spec", "varchar(50)"),
        ("lookup_spec", "varchar(50)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
    "w_beam": (
        ("us_spec1", "varchar(30)"),
        ("us_spec2", "varchar(30)"),
        ("cn_spec", "varchar(60)"),
        ("lookup_us_spec", "varchar(60)"),
        ("lookup_cn_spec", "varchar(60)"),
        ("cross_section_area", "decimal(18,9)"),
        ("height", "decimal(18,9)"),
        ("flange_width", "decimal(18,9)"),
        ("web_thickness", "decimal(18,9)"),
        ("flange_thickness", "decimal(18,9)"),
        ("weight", "decimal(18,9)"),
        ("lookup_enabled", "boolean"),
    ),
}


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (int, float)):
        return repr(value)
    escaped = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


def _insert_rows(
    table: str,
    columns: tuple[str, ...],
    rows: Iterable[tuple[object, ...]],
) -> list[str]:
    values = list(rows)
    if not values:
        return []
    statements = []
    for start in range(0, len(values), 250):
        chunk = values[start : start + 250]
        rendered = ",\n".join(
            "(" + ",".join(_sql_literal(value) for value in row) + ")"
            for row in chunk
        )
        statements.append(
            f"INSERT INTO `{table}` "
            f"({','.join(f'`{column}`' for column in columns)}) VALUES\n"
            f"{rendered};"
        )
    return statements


def render_database_sql(
    snapshot: HandbookSnapshot,
    *,
    database_name: str = "hardware_handbook",
) -> str:
    if re.fullmatch(r"[A-Za-z0-9_]+", database_name) is None:
        raise ValueError("database name may contain only letters, digits, and underscores")
    lines = [
        "-- Generated only from the reviewed hardware-handbook workbook.",
        f"-- Source: {snapshot.source_path}",
        f"-- SHA-256: {snapshot.sha256}",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS=0;",
        f"DROP DATABASE IF EXISTS `{database_name}`;",
        f"CREATE DATABASE `{database_name}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
        f"USE `{database_name}`;",
        "CREATE TABLE `source_workbook` (",
        "  `id` tinyint NOT NULL,",
        "  `sha256` char(64) NOT NULL,",
        "  `source_path` text NOT NULL,",
        "  `file_name` varchar(255) NOT NULL,",
        "  `file_size` bigint NOT NULL,",
        "  `sheet_count` int NOT NULL,",
        "  PRIMARY KEY (`id`),",
        "  UNIQUE KEY `uk_source_sha256` (`sha256`)",
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;",
        "CREATE TABLE `source_row` (",
        "  `id` bigint NOT NULL,",
        "  `workbook_id` tinyint NOT NULL,",
        "  `sheet_index` int NOT NULL,",
        "  `sheet_name` varchar(255) NOT NULL,",
        "  `row_number` int NOT NULL,",
        "  `record_type` varchar(50) NOT NULL,",
        "  `raw_values` json NOT NULL,",
        "  PRIMARY KEY (`id`),",
        "  UNIQUE KEY `uk_source_coordinate` (`workbook_id`,`sheet_index`,`row_number`),",
        "  CONSTRAINT `fk_source_row_workbook` FOREIGN KEY (`workbook_id`) "
        "REFERENCES `source_workbook` (`id`)",
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;",
    ]
    for table, columns in _TABLE_COLUMNS.items():
        column_lines = [
            "  `source_row_id` bigint NOT NULL",
            *(
                f"  `{column}` {sql_type} {'NOT NULL' if column == 'lookup_enabled' else 'NULL'}"
                for column, sql_type in columns
            ),
            "  PRIMARY KEY (`source_row_id`)",
            f"  CONSTRAINT `fk_{table}_source_row` FOREIGN KEY (`source_row_id`) "
            "REFERENCES `source_row` (`id`)",
        ]
        for column, _ in columns:
            if column.startswith("lookup_") and column != "lookup_enabled":
                column_lines.insert(
                    -1,
                    f"  KEY `idx_{column}` (`{column}`,`lookup_enabled`)",
                )
        lines.extend(
            [
                f"CREATE TABLE `{table}` (",
                ",\n".join(column_lines),
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;",
            ]
        )
    lines.extend(
        _insert_rows(
            "source_workbook",
            ("id", "sha256", "source_path", "file_name", "file_size", "sheet_count"),
            (
                (
                    1,
                    snapshot.sha256,
                    str(snapshot.source_path),
                    snapshot.source_path.name,
                    snapshot.file_size,
                    snapshot.sheet_count,
                ),
            ),
        )
    )
    lines.extend(
        _insert_rows(
            "source_row",
            (
                "id",
                "workbook_id",
                "sheet_index",
                "sheet_name",
                "row_number",
                "record_type",
                "raw_values",
            ),
            (
                (
                    row.source_row_id,
                    1,
                    row.sheet_index,
                    row.sheet_name,
                    row.row_number,
                    row.record_type,
                    json.dumps(
                        [_json_value(value) for value in row.raw_values],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
                for row in snapshot.source_rows
            ),
        )
    )
    for table, columns in _TABLE_COLUMNS.items():
        records = snapshot.table_records[table]
        names = tuple(column for column, _ in columns)
        lines.extend(
            _insert_rows(
                table,
                ("source_row_id", *names),
                (
                    (
                        record.source_row_id,
                        *(record.values[name] for name in names),
                    )
                    for record in records
                ),
            )
        )
    lines.extend(
        [
            "SET FOREIGN_KEY_CHECKS=1;",
            "",
        ]
    )
    return "\n".join(lines)


def expected_database_manifest(snapshot: HandbookSnapshot) -> Mapping[str, int]:
    return MappingProxyType(
        {
            "source_workbook": 1,
            "source_row": len(snapshot.source_rows),
            **{
                table: len(records)
                for table, records in snapshot.table_records.items()
            },
        }
    )


def compare_database_manifest(
    snapshot: HandbookSnapshot,
    actual_manifest: Mapping[str, int],
) -> tuple[str, ...]:
    expected = expected_database_manifest(snapshot)
    problems = [
        f"unexpected table {table} has {actual_manifest[table]} rows"
        for table in sorted(set(actual_manifest) - set(expected))
    ]
    problems.extend(
        f"missing table {table}"
        for table in sorted(set(expected) - set(actual_manifest))
    )
    problems.extend(
        f"table {table} expected {expected[table]} rows but found {actual_manifest[table]}"
        for table in sorted(set(expected) & set(actual_manifest))
        if expected[table] != actual_manifest[table]
    )
    return tuple(problems)


_SOURCE_WORKBOOK_COLUMNS = (
    "id",
    "sha256",
    "source_path",
    "file_name",
    "file_size",
    "sheet_count",
)
_SOURCE_ROW_COLUMNS = (
    "id",
    "workbook_id",
    "sheet_index",
    "sheet_name",
    "row_number",
    "record_type",
    "raw_values",
)


def _canonical_json(value: object) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def expected_database_content(snapshot: HandbookSnapshot) -> DatabaseContent:
    """Build the exact database representation expected from one source snapshot."""

    rows: dict[str, tuple[tuple[object, ...], ...]] = {
        "source_workbook": (
            (
                1,
                snapshot.sha256,
                str(snapshot.source_path),
                snapshot.source_path.name,
                snapshot.file_size,
                snapshot.sheet_count,
            ),
        ),
        "source_row": tuple(
            (
                row.source_row_id,
                1,
                row.sheet_index,
                row.sheet_name,
                row.row_number,
                row.record_type,
                json.dumps(
                    [_json_value(value) for value in row.raw_values],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            for row in snapshot.source_rows
        ),
    }
    for table, columns in _TABLE_COLUMNS.items():
        names = tuple(column for column, _ in columns)
        rows[table] = tuple(
            (
                record.source_row_id,
                *(record.values[name] for name in names),
            )
            for record in snapshot.table_records[table]
        )
    return DatabaseContent(
        manifest=expected_database_manifest(snapshot),
        table_rows=MappingProxyType(rows),
    )


def _table_columns(table: str) -> tuple[str, ...]:
    if table == "source_workbook":
        return _SOURCE_WORKBOOK_COLUMNS
    if table == "source_row":
        return _SOURCE_ROW_COLUMNS
    return (
        "source_row_id",
        *(column for column, _ in _TABLE_COLUMNS[table]),
    )


def read_database_content(connection: Any) -> DatabaseContent:
    """Read deployed rows without deriving or repairing any handbook value."""

    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        selected = cursor.fetchone()
        database_name = selected[0] if selected else None
        if not database_name:
            raise ValueError("handbook database connection has no selected database")
        cursor.execute(
            "SELECT `TABLE_NAME` FROM `information_schema`.`TABLES` "
            "WHERE `TABLE_SCHEMA` = %s AND `TABLE_TYPE` = 'BASE TABLE' "
            "ORDER BY `TABLE_NAME`",
            (database_name,),
        )
        table_names = tuple(row[0] for row in cursor.fetchall())
        manifest: dict[str, int] = {}
        for table in table_names:
            escaped_table = str(table).replace("`", "``")
            cursor.execute(f"SELECT COUNT(*) FROM `{escaped_table}`")
            manifest[str(table)] = int(cursor.fetchone()[0])

        expected_tables = set(_TABLE_COLUMNS) | {"source_workbook", "source_row"}
        table_rows: dict[str, tuple[tuple[object, ...], ...]] = {}
        for table in sorted(expected_tables & set(table_names)):
            columns = _table_columns(table)
            select_columns = ",".join(f"`{column}`" for column in columns)
            order_column = "id" if table in {"source_workbook", "source_row"} else "source_row_id"
            cursor.execute(
                f"SELECT {select_columns} FROM `{table}` ORDER BY `{order_column}`"
            )
            records = [tuple(row) for row in cursor.fetchall()]
            if table == "source_row":
                records = [
                    (*row[:6], _canonical_json(row[6]))
                    for row in records
                ]
            table_rows[table] = tuple(records)
    return DatabaseContent(
        manifest=MappingProxyType(manifest),
        table_rows=MappingProxyType(table_rows),
    )


def compare_database_content(
    snapshot: HandbookSnapshot,
    actual: DatabaseContent,
    *,
    max_value_problems: int = 50,
) -> tuple[str, ...]:
    """Compare every stored value, while keeping a corrupt-database report bounded."""

    problems = list(compare_database_manifest(snapshot, actual.manifest))
    expected = expected_database_content(snapshot)
    value_problem_count = 0
    common_tables = sorted(set(expected.table_rows) & set(actual.table_rows))
    for table in common_tables:
        expected_rows = expected.table_rows[table]
        actual_rows = actual.table_rows[table]
        if len(expected_rows) != len(actual_rows):
            continue
        columns = _table_columns(table)
        for expected_row, actual_row in zip(expected_rows, actual_rows, strict=True):
            if expected_row == actual_row:
                continue
            row_key = expected_row[0] if expected_row else "unknown"
            for index, column in enumerate(columns):
                expected_value = expected_row[index]
                actual_value = actual_row[index] if index < len(actual_row) else "<missing>"
                if expected_value != actual_value:
                    problems.append(
                        f"table {table} row {row_key} column {column} "
                        f"expected {expected_value!r} but found {actual_value!r}"
                    )
                    value_problem_count += 1
                    break
            if value_problem_count >= max_value_problems:
                problems.append(
                    f"value comparison stopped after {max_value_problems} differences"
                )
                return tuple(problems)
    return tuple(problems)


def audit_database_connection(
    snapshot: HandbookSnapshot,
    connection: Any,
) -> tuple[str, ...]:
    """Return no problems only when the deployed database is source-exact."""

    return compare_database_content(snapshot, read_database_content(connection))
