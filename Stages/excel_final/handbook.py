"""Category-aware, read-only MySQL repository for steel handbook weights."""

from __future__ import annotations

import logging
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


@dataclass(frozen=True, slots=True)
class HandbookLookupResult:
    category: str
    normalized_spec: str
    value_kg_per_m: Decimal | None
    source: str
    status: LookupStatus


@dataclass(frozen=True, slots=True)
class _QueryDefinition:
    table: str
    sql: str


_QUERIES: Mapping[str, _QueryDefinition] = {
    HandbookCategory.FLAT_STEEL.value: _QueryDefinition(
        "flat_steel",
        "SELECT weight FROM `flat_steel` WHERE spec = %s LIMIT 1",
    ),
    HandbookCategory.ROUND_BAR.value: _QueryDefinition(
        "round_square_bar",
        "SELECT round_weight FROM `round_square_bar` WHERE dia_or_side = %s LIMIT 1",
    ),
    HandbookCategory.REBAR.value: _QueryDefinition(
        "rebar",
        "SELECT weight FROM `rebar` WHERE dia = %s LIMIT 1",
    ),
    HandbookCategory.SQUARE_BAR.value: _QueryDefinition(
        "round_square_bar",
        "SELECT square_weight FROM `round_square_bar` WHERE dia_or_side = %s LIMIT 1",
    ),
    HandbookCategory.I_BEAM.value: _QueryDefinition(
        "i_beam",
        "SELECT weight FROM `i_beam` WHERE spec = %s LIMIT 1",
    ),
    HandbookCategory.H_BEAM.value: _QueryDefinition(
        "h_beam",
        "SELECT weight_2010, weight_2005, weight_98 FROM `h_beam` WHERE spec = %s LIMIT 1",
    ),
    HandbookCategory.T_BEAM.value: _QueryDefinition(
        "t_beam",
        "SELECT weight_2010, weight_2005, weight_98 FROM `t_beam` WHERE spec = %s LIMIT 1",
    ),
    HandbookCategory.CHANNEL.value: _QueryDefinition(
        "channel",
        "SELECT weight FROM `channel` WHERE spec = %s LIMIT 1",
    ),
    HandbookCategory.ANGLE.value: _QueryDefinition(
        "angle",
        "SELECT weight FROM `angle` WHERE spec = %s LIMIT 1",
    ),
    HandbookCategory.STEEL_PIPE.value: _QueryDefinition(
        "steel_pipe",
        "SELECT weight FROM `steel_pipe` WHERE spec = %s LIMIT 1",
    ),
    HandbookCategory.SQUARE_TUBE.value: _QueryDefinition(
        "square_tube",
        "SELECT weight FROM `square_tube` WHERE spec = %s LIMIT 1",
    ),
    HandbookCategory.HFW_PIPE.value: _QueryDefinition(
        "hfw_pipe",
        "SELECT weight FROM `hfw_pipe` WHERE spec = %s LIMIT 1",
    ),
    HandbookCategory.W_BEAM.value: _QueryDefinition(
        "w_beam",
        "SELECT weight FROM `w_beam` "
        "WHERE us_spec1 = %s OR us_spec2 = %s OR cn_spec = %s LIMIT 1",
    ),
}

_SCHEMA_SQL = (
    "SELECT spec, weight FROM `flat_steel` LIMIT 0",
    "SELECT dia_or_side, round_weight, square_weight FROM `round_square_bar` LIMIT 0",
    "SELECT dia, weight FROM `rebar` LIMIT 0",
    "SELECT spec, weight FROM `i_beam` LIMIT 0",
    "SELECT spec, weight_2010, weight_2005, weight_98 FROM `h_beam` LIMIT 0",
    "SELECT spec, weight_2010, weight_2005, weight_98 FROM `t_beam` LIMIT 0",
    "SELECT spec, weight FROM `channel` LIMIT 0",
    "SELECT spec, weight FROM `angle` LIMIT 0",
    "SELECT spec, weight FROM `steel_pipe` LIMIT 0",
    "SELECT spec, weight FROM `square_tube` LIMIT 0",
    "SELECT spec, weight FROM `hfw_pipe` LIMIT 0",
    "SELECT us_spec1, us_spec2, cn_spec, weight FROM `w_beam` LIMIT 0",
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
    if normalized.startswith("Q355B"):
        return "Q355B"
    return None


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
        self.stats = {"hit": 0, "not_found": 0, "skipped": 0}
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
            if material_class not in {"HPB", "Q355B"}:
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
        params: tuple[object, ...]
        if category_value == HandbookCategory.W_BEAM.value:
            params = (spec, spec, spec)
        else:
            params = (spec,)
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(definition.sql, params)
                row = cursor.fetchone()
        except Exception as exc:
            raise HandbookInfrastructureError(
                f"handbook query failed for category {category_value}"
            ) from exc

        if row is None:
            result = HandbookLookupResult(
                category=category_value,
                normalized_spec=spec,
                value_kg_per_m=None,
                source=f"{definition.table}:not_found",
                status=LookupStatus.NOT_FOUND,
            )
            self.stats["not_found"] += 1
        else:
            raw_weight = next((value for value in row if value is not None), None)
            if raw_weight is None:
                raise HandbookInfrastructureError(
                    f"handbook query returned no usable weight for category {category_value}"
                )
            result = HandbookLookupResult(
                category=category_value,
                normalized_spec=spec,
                value_kg_per_m=_decimal_weight(raw_weight),
                source=f"{definition.table}:{category_value}",
                status=LookupStatus.HIT,
            )
            self.stats["hit"] += 1
        self._cache[cache_key] = result
        return result

    def log_stats(self) -> None:
        log.info(
            "五金手册查询统计: hit=%d, not_found=%d, skipped=%d",
            self.stats["hit"],
            self.stats["not_found"],
            self.stats["skipped"],
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
