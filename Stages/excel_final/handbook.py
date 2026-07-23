"""Category-aware, read-only MySQL repository for steel handbook weights."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Callable, Mapping

import pymysql

from config import DB_CONFIG
from spec_parser import HandbookCategory

log = logging.getLogger(__name__)


class HandbookRequestError(ValueError):
    """The caller did not provide a coherent category/spec/material request."""


class HandbookInfrastructureError(RuntimeError):
    """The handbook service is unavailable or its schema/query contract is broken."""


class LookupStatus(StrEnum):
    HIT = "hit"
    NOT_FOUND = "not_found"
    SKIPPED = "skipped"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class HandbookLookupResult:
    category: str
    normalized_spec: str
    value_kg_per_m: Decimal | None
    source: str
    status: LookupStatus
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _QueryDefinition:
    table: str
    sql: str


_QUERIES: Mapping[str, _QueryDefinition] = {
    HandbookCategory.FLAT_STEEL.value: _QueryDefinition(
        "flat_steel",
        "SELECT t.weight, s.`sheet_name`, s.`row_number` "
        "FROM `flat_steel` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.lookup_spec = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.ROUND_BAR.value: _QueryDefinition(
        "round_square_bar",
        "SELECT t.round_weight, s.`sheet_name`, s.`row_number` "
        "FROM `round_square_bar` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.diameter_or_side = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.REBAR.value: _QueryDefinition(
        "rebar",
        "SELECT t.weight, s.`sheet_name`, s.`row_number` "
        "FROM `rebar` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.nominal_diameter = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.SQUARE_BAR.value: _QueryDefinition(
        "round_square_bar",
        "SELECT t.square_weight, s.`sheet_name`, s.`row_number` "
        "FROM `round_square_bar` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.diameter_or_side = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.I_BEAM.value: _QueryDefinition(
        "i_beam",
        "SELECT t.weight, s.`sheet_name`, s.`row_number` "
        "FROM `i_beam` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.lookup_spec = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.H_BEAM.value: _QueryDefinition(
        "h_beam",
        "SELECT t.weight_2010, t.weight_2005, t.weight_98, s.`sheet_name`, s.`row_number` "
        "FROM `h_beam` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.lookup_spec = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.T_BEAM.value: _QueryDefinition(
        "t_beam",
        "SELECT t.weight_2010, t.weight_2005, t.weight_98, s.`sheet_name`, s.`row_number` "
        "FROM `t_beam` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.lookup_spec = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.CHANNEL.value: _QueryDefinition(
        "channel",
        "SELECT t.weight, s.`sheet_name`, s.`row_number` "
        "FROM `channel` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.lookup_spec = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.ANGLE.value: _QueryDefinition(
        "angle",
        "SELECT t.weight, s.`sheet_name`, s.`row_number` "
        "FROM `angle` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.lookup_spec = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.STEEL_PIPE.value: _QueryDefinition(
        "steel_pipe",
        "SELECT t.weight, s.`sheet_name`, s.`row_number` "
        "FROM `steel_pipe` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.lookup_spec = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.SQUARE_TUBE.value: _QueryDefinition(
        "square_tube",
        "SELECT t.weight, s.`sheet_name`, s.`row_number` "
        "FROM `square_tube` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.lookup_spec = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.HFW_PIPE.value: _QueryDefinition(
        "hfw_pipe",
        "SELECT t.weight, s.`sheet_name`, s.`row_number` "
        "FROM `hfw_pipe` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.lookup_spec = %s AND t.lookup_enabled = 1 ORDER BY t.source_row_id",
    ),
    HandbookCategory.W_BEAM.value: _QueryDefinition(
        "w_beam",
        "SELECT t.weight, s.`sheet_name`, s.`row_number` "
        "FROM `w_beam` AS t JOIN `source_row` AS s ON s.id = t.source_row_id "
        "WHERE t.lookup_enabled = 1 "
        "AND (t.lookup_us_spec = %s OR t.lookup_cn_spec = %s) "
        "ORDER BY t.source_row_id",
    ),
}

_SCHEMA_SQL = (
    "SELECT `id`, `sheet_name`, `row_number` FROM `source_row` LIMIT 0",
    "SELECT source_row_id, lookup_spec, weight, lookup_enabled FROM `flat_steel` LIMIT 0",
    "SELECT source_row_id, diameter_or_side, round_weight, square_weight, lookup_enabled "
    "FROM `round_square_bar` LIMIT 0",
    "SELECT source_row_id, nominal_diameter, weight, lookup_enabled FROM `rebar` LIMIT 0",
    "SELECT source_row_id, lookup_spec, weight, lookup_enabled FROM `i_beam` LIMIT 0",
    "SELECT source_row_id, lookup_spec, weight_2010, weight_2005, weight_98, lookup_enabled "
    "FROM `h_beam` LIMIT 0",
    "SELECT source_row_id, lookup_spec, weight_2010, weight_2005, weight_98, lookup_enabled "
    "FROM `t_beam` LIMIT 0",
    "SELECT source_row_id, lookup_spec, weight, lookup_enabled FROM `channel` LIMIT 0",
    "SELECT source_row_id, lookup_spec, weight, lookup_enabled FROM `angle` LIMIT 0",
    "SELECT source_row_id, lookup_spec, weight, lookup_enabled FROM `steel_pipe` LIMIT 0",
    "SELECT source_row_id, lookup_spec, weight, lookup_enabled FROM `square_tube` LIMIT 0",
    "SELECT source_row_id, lookup_spec, weight, lookup_enabled FROM `hfw_pipe` LIMIT 0",
    "SELECT source_row_id, us_spec1, us_spec2, cn_spec, lookup_us_spec, "
    "lookup_cn_spec, weight, lookup_enabled "
    "FROM `w_beam` LIMIT 0",
)

_SPECIAL_CATEGORIES = {"plate", "skip"}
_Connector = Callable[..., Any]


def _category_value(category: HandbookCategory | str) -> str:
    if isinstance(category, HandbookCategory):
        return category.value
    return str(category).strip()


def _material_class(material: str | None) -> str | None:
    normalized = str(material or "").replace(" ", "").replace("　", "").upper()
    if normalized.startswith("HRB"):
        return "HRB"
    if normalized.startswith("HPB"):
        return "HPB"
    if normalized.startswith("Q235B"):
        return "Q235B"
    if normalized.startswith("Q355B"):
        return "Q355B"
    return None


def _database_spec(category: str, source_spec: str) -> str:
    """Translate supported drawing aliases to the handbook's stored key only."""
    upper = str(source_spec).replace(" ", "").replace("　", "").upper()
    upper = re.sub(r"(?<=\d)[X×](?=\d)", "*", upper)
    if category == HandbookCategory.I_BEAM.value:
        match = re.fullmatch(r"HI(\d.*)", upper)
        if match is not None:
            return f"I{match.group(1)}"
    if category == HandbookCategory.H_BEAM.value:
        match = re.fullmatch(r"(?:HN|HW|HM|HT)(\d.*)", upper)
        if match is not None:
            return f"H{match.group(1)}"
    if category == HandbookCategory.T_BEAM.value:
        match = re.fullmatch(r"(?:TN|TW|TM)(\d.*)", upper)
        if match is not None:
            return f"T{match.group(1)}"
    if category == HandbookCategory.CHANNEL.value:
        match = re.fullmatch(r"C(\d+(?:\.\d+)?[A-Z]?)", upper)
        if match is not None:
            return f"[{match.group(1)}"
    if category == HandbookCategory.STEEL_PIPE.value:
        match = re.fullmatch(
            r"(?:PIP|IP|P|Φ|D)(\d+(?:\.\d+)?)\*(\d+(?:\.\d+)?)",
            upper,
        )
        if match is not None:
            return f"φ{_canonical_number(match.group(1))}*{_canonical_number(match.group(2))}"
    if category == HandbookCategory.SQUARE_TUBE.value:
        body = re.sub(r"^(?:方管|矩形管|□)", "", upper)
        dimensions = body.split("*")
        if len(dimensions) == 2 and all(_is_number(value) for value in dimensions):
            side, thickness = (_canonical_number(value) for value in dimensions)
            return f"□{side}*{thickness}"
        if len(dimensions) == 3 and all(_is_number(value) for value in dimensions):
            side_a, side_b, thickness = (
                _canonical_number(value) for value in dimensions
            )
            if Decimal(side_a) == Decimal(side_b):
                return f"□{side_a}*{thickness}"
            return f"□{side_a}*{side_b}*{thickness}"
    if category == HandbookCategory.SQUARE_BAR.value:
        body = re.sub(r"^方钢", "", upper)
        dimensions = body.split("*")
        if dimensions and all(_is_number(value) for value in dimensions):
            sides = tuple(_canonical_number(value) for value in dimensions)
            if len(sides) == 1 or (len(sides) == 2 and sides[0] == sides[1]):
                return sides[0]
    if category == HandbookCategory.HFW_PIPE.value:
        match = re.fullmatch(r"HFW(\d.*)", upper)
        if match is not None:
            return f"LH{match.group(1)}"
    return upper


def _is_number(value: str) -> bool:
    return re.fullmatch(r"\d+(?:\.\d+)?", value) is not None


def _canonical_number(value: str) -> str:
    number = Decimal(value)
    rendered = format(number, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _decimal_weight(value: object) -> Decimal:
    try:
        weight = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HandbookInfrastructureError("handbook query returned a non-numeric weight") from exc
    if weight <= 0 or weight > Decimal("2000"):
        raise HandbookInfrastructureError("handbook query returned an out-of-range weight")
    return weight


class SteelHandbookRepository:
    """Execute one allow-listed table query for each confirmed material category."""

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        connector: _Connector = pymysql.connect,
    ) -> None:
        self.config = dict(config)
        self._cache: dict[tuple[str, str, str | None], HandbookLookupResult] = {}
        self.stats = {"hit": 0, "not_found": 0, "skipped": 0, "conflict": 0}
        try:
            self.conn = connector(**self.config)
        except Exception as exc:
            raise HandbookInfrastructureError("unable to connect to handbook database") from exc
        try:
            self._validate_schema()
        except Exception:
            try:
                self.conn.close()
            finally:
                raise

    def _validate_schema(self) -> None:
        try:
            with self.conn.cursor() as cursor:
                for sql in _SCHEMA_SQL:
                    cursor.execute(sql)
        except Exception as exc:
            raise HandbookInfrastructureError("handbook schema validation failed") from exc

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception as exc:
            raise HandbookInfrastructureError("unable to close handbook connection") from exc

    def lookup(
        self,
        category: HandbookCategory | str,
        normalized_spec: str,
        *,
        material: str | None = None,
    ) -> HandbookLookupResult:
        category_value = _category_value(category)
        spec = str(normalized_spec or "").strip()
        if not category_value:
            raise HandbookRequestError("category is required")
        if not spec:
            raise HandbookRequestError("spec is required")
        if category_value not in _QUERIES and category_value not in _SPECIAL_CATEGORIES:
            raise HandbookRequestError(f"unsupported category: {category_value}")

        material_class = _material_class(material)
        if category_value == HandbookCategory.ROUND_BAR.value:
            if material_class is None:
                raise HandbookRequestError("round_bar material is required")
            if material_class not in {"HPB", "Q235B", "Q355B"}:
                raise HandbookRequestError("round_bar category conflicts with material")
        elif category_value == HandbookCategory.REBAR.value:
            if material_class is None:
                raise HandbookRequestError("rebar material is required")
            if material_class != "HRB":
                raise HandbookRequestError("rebar category conflicts with material")

        cache_key = (category_value, spec, material_class)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if category_value == "plate":
            result = HandbookLookupResult(
                category=category_value,
                normalized_spec=spec,
                value_kg_per_m=Decimal("7.85"),
                source="plate_constant:7.85",
                status=LookupStatus.HIT,
            )
            self._cache[cache_key] = result
            self.stats["hit"] += 1
            return result
        if category_value == "skip":
            result = HandbookLookupResult(
                category=category_value,
                normalized_spec=spec,
                value_kg_per_m=None,
                source="explicit_skip",
                status=LookupStatus.SKIPPED,
            )
            self._cache[cache_key] = result
            self.stats["skipped"] += 1
            return result

        definition = _QUERIES[category_value]
        database_spec = _database_spec(category_value, spec)
        params: tuple[object, ...]
        if category_value == HandbookCategory.W_BEAM.value:
            params = (database_spec, database_spec)
        else:
            params = (database_spec,)
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(definition.sql, params)
                rows = cursor.fetchall()
        except Exception as exc:
            raise HandbookInfrastructureError(
                f"handbook query failed for category {category_value}"
            ) from exc

        if not rows:
            result = HandbookLookupResult(
                category=category_value,
                normalized_spec=spec,
                value_kg_per_m=None,
                source=f"{definition.table}:not_found",
                status=LookupStatus.NOT_FOUND,
            )
            self.stats["not_found"] += 1
        else:
            weights: set[Decimal] = set()
            source_refs: list[str] = []
            for row in rows:
                values = tuple(row)
                if (
                    len(values) >= 3
                    and isinstance(values[-2], str)
                    and isinstance(values[-1], int)
                ):
                    weight_values = values[:-2]
                    source_refs.append(f"{values[-2]}!{values[-1]}")
                else:
                    # Compatibility for isolated repository tests using a compact row.
                    weight_values = values
                raw_weight = next(
                    (value for value in weight_values if value is not None),
                    None,
                )
                if raw_weight is None:
                    raise HandbookInfrastructureError(
                        f"handbook query returned no usable weight for category {category_value}"
                    )
                weights.add(_decimal_weight(raw_weight))
            refs = tuple(dict.fromkeys(source_refs))
            if len(weights) > 1:
                result = HandbookLookupResult(
                    category=category_value,
                    normalized_spec=spec,
                    value_kg_per_m=None,
                    source=f"{definition.table}:conflict",
                    status=LookupStatus.CONFLICT,
                    source_refs=refs,
                )
                self.stats["conflict"] += 1
            else:
                result = HandbookLookupResult(
                    category=category_value,
                    normalized_spec=spec,
                    value_kg_per_m=next(iter(weights)),
                    source=f"{definition.table}:{category_value}",
                    status=LookupStatus.HIT,
                    source_refs=refs,
                )
                self.stats["hit"] += 1
        self._cache[cache_key] = result
        return result

    def log_stats(self) -> None:
        log.info(
            "五金手册查询统计: hit=%d, not_found=%d, skipped=%d, conflict=%d",
            self.stats["hit"],
            self.stats["not_found"],
            self.stats["skipped"],
            self.stats["conflict"],
        )


# Transitional class name retained for the isolated Stage runner import.
SteelHandbookDB = SteelHandbookRepository

_repository: SteelHandbookRepository | None = None


def init_handbook(config: Mapping[str, object] | None = None) -> SteelHandbookRepository:
    global _repository
    effective = dict(config if config is not None else DB_CONFIG)
    if not effective:
        raise HandbookInfrastructureError("handbook database configuration is required")
    _repository = SteelHandbookRepository(effective)
    return _repository


def close_handbook() -> None:
    global _repository
    if _repository is not None:
        _repository.close()
        _repository = None
