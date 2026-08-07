"""Pure BOX setback domain rules used by Excel Final Stage 2.

Mirrors bh_stage2.py: the BOX reader emits `翼`/`腹` (upper/lower identical,
merged ×2) or `上翼`/`下翼`/`上腹`/`下腹` (split ×1), exactly like the
splitter's PhysicalPlateRole equivalence groups (FLANGE_TOP+FLANGE_BOTTOM ->
翼×2, WEB_LEFT+WEB_RIGHT -> 腹×2).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
import re
from types import MappingProxyType
import unicodedata
from typing import Mapping

from canonical_pipeline import CanonicalProjection
from fabricated_profile import FabricatedProfile, parse_fabricated_profile
from part_builder import PartCandidate
from quality import IssueLevel, QualityIssue
from weights import plate_unit_weight, round_weight_for_output


BOX_MEASUREMENT_SCHEMA = "box_setback_measurements/v1"


class BoxMeasurementContractError(ValueError):
    """The internal Reader-to-Excel payload does not match its fixed schema."""


class BoxDuplicatePartDrawingError(ValueError):
    code = "EXCEL_STAGE2_DUPLICATE_PART_DRAWING"

    def __init__(self, conflicts: Mapping[str, tuple[str, ...]]) -> None:
        self.conflicts = dict(conflicts)
        rendered = "；".join(
            f"{part_number}: {', '.join(file_names)}"
            for part_number, file_names in self.conflicts.items()
        )
        super().__init__(
            "同一 BOX 零件号存在多张图纸，系统未自动选取：" + rendered
        )


@dataclass(frozen=True, slots=True)
class BoxSetbackMeasurement:
    role: str
    left_safe: Decimal
    right_safe: Decimal


@dataclass(frozen=True, slots=True)
class BoxDrawingMeasurement:
    source_file_id: int
    file_name: str
    part_number: str
    classification_spec: str
    reader_spec: str
    status: str
    warnings: tuple[str, ...]
    measurements: tuple[BoxSetbackMeasurement, ...]


@dataclass(frozen=True, slots=True)
class BoxMeasurementContract:
    schema: str
    items: tuple[BoxDrawingMeasurement, ...]


@dataclass(frozen=True, slots=True)
class BoxPlatePlan:
    source_roles: tuple[str, ...]
    part_type: str
    import_part_no: str
    spec: Decimal
    width: Decimal
    model_length: Decimal
    left_safe: Decimal
    right_safe: Decimal
    cut_length: Decimal
    material: str
    quantity_multiplier: Decimal


@dataclass(frozen=True, slots=True)
class BoxEnhancementResult:
    projection: CanonicalProjection
    status: str
    matched_occurrence_count: int
    missing_drawing_count: int
    unmatched_drawing_count: int
    manual_occurrence_count: int


@dataclass(frozen=True, slots=True)
class BoxRoleMapping:
    role: str
    part_type: str
    import_part_no: str
    quantity_multiplier: Decimal
    sort_key: tuple[int, int]


_TOP_LEVEL_FIELDS = frozenset({"schema", "items"})
_DRAWING_FIELDS = frozenset({
    "source_file_id",
    "file_name",
    "part_number",
    "classification_spec",
    "reader_spec",
    "status",
    "warnings",
    "measurements",
})
_MEASUREMENT_FIELDS = frozenset({"role", "left_safe", "right_safe"})


def _normalized_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def normalized_part_number(value: object) -> str:
    return _normalized_text(value).casefold()


def _exact_fields(
    value: object,
    expected: frozenset[str],
    *,
    path: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BoxMeasurementContractError(f"{path} 必须是对象")
    actual = frozenset(value.keys())
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected, key=str)
        raise BoxMeasurementContractError(
            f"{path} 字段不符合 {BOX_MEASUREMENT_SCHEMA}: "
            f"缺少={missing!r}, 未知={unknown!r}"
        )
    return value


def _required_text(value: object, *, path: str, allow_blank: bool = False) -> str:
    if not isinstance(value, str):
        raise BoxMeasurementContractError(f"{path} 必须是字符串")
    normalized = _normalized_text(value)
    if not normalized and not allow_blank:
        raise BoxMeasurementContractError(f"{path} 不能为空")
    return normalized


def _decimal(value: object, *, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise BoxMeasurementContractError(f"{path} 必须是数值")
    try:
        return Decimal(str(value))
    except ArithmeticError as exc:
        raise BoxMeasurementContractError(f"{path} 不是可解析数值") from exc


def parse_box_measurement_contract(payload: object) -> BoxMeasurementContract:
    """Parse the compact, versioned Reader result used by Stage 2.

    The backend publishes BOX reader results in the same field layout as the
    BH contract (source_file_id / classification_spec / reader_spec included),
    so the parser mirrors bh_stage2; only the schema value differs.
    """
    root = _exact_fields(payload, _TOP_LEVEL_FIELDS, path="BOX左右进合同")
    if root["schema"] != BOX_MEASUREMENT_SCHEMA:
        raise BoxMeasurementContractError(
            f"BOX左右进合同版本必须是 {BOX_MEASUREMENT_SCHEMA!r}"
        )
    raw_items = root["items"]
    if not isinstance(raw_items, list):
        raise BoxMeasurementContractError("BOX左右进合同.items 必须是数组")

    drawings: list[BoxDrawingMeasurement] = []
    for item_index, raw_item in enumerate(raw_items):
        path = f"BOX左右进合同.items[{item_index}]"
        item = _exact_fields(raw_item, _DRAWING_FIELDS, path=path)
        source_file_id = item["source_file_id"]
        if (
            isinstance(source_file_id, bool)
            or not isinstance(source_file_id, int)
            or source_file_id <= 0
        ):
            raise BoxMeasurementContractError(f"{path}.source_file_id 必须是正整数")
        warnings = item["warnings"]
        if not isinstance(warnings, list) or not all(
            isinstance(warning, str) for warning in warnings
        ):
            raise BoxMeasurementContractError(f"{path}.warnings 必须是字符串数组")
        raw_measurements = item["measurements"]
        if not isinstance(raw_measurements, list):
            raise BoxMeasurementContractError(f"{path}.measurements 必须是数组")
        measurements: list[BoxSetbackMeasurement] = []
        for measurement_index, raw_measurement in enumerate(raw_measurements):
            measurement_path = f"{path}.measurements[{measurement_index}]"
            measurement = _exact_fields(
                raw_measurement,
                _MEASUREMENT_FIELDS,
                path=measurement_path,
            )
            measurements.append(BoxSetbackMeasurement(
                role=_required_text(
                    measurement["role"],
                    path=f"{measurement_path}.role",
                    allow_blank=True,
                ),
                left_safe=_decimal(
                    measurement["left_safe"],
                    path=f"{measurement_path}.left_safe",
                ),
                right_safe=_decimal(
                    measurement["right_safe"],
                    path=f"{measurement_path}.right_safe",
                ),
            ))
        drawings.append(BoxDrawingMeasurement(
            source_file_id=source_file_id,
            file_name=_required_text(item["file_name"], path=f"{path}.file_name"),
            part_number=_required_text(
                item["part_number"],
                path=f"{path}.part_number",
            ),
            classification_spec=_required_text(
                item["classification_spec"],
                path=f"{path}.classification_spec",
            ),
            reader_spec=_required_text(
                item["reader_spec"],
                path=f"{path}.reader_spec",
                allow_blank=True,
            ),
            status=_required_text(item["status"], path=f"{path}.status"),
            warnings=tuple(warnings),
            measurements=tuple(measurements),
        ))
    source_file_ids: set[int] = set()
    duplicate_source_file_ids: set[int] = set()
    for drawing in drawings:
        if drawing.source_file_id in source_file_ids:
            duplicate_source_file_ids.add(drawing.source_file_id)
        source_file_ids.add(drawing.source_file_id)
    if duplicate_source_file_ids:
        raise BoxMeasurementContractError(
            "BOX左右进合同 source_file_id 重复: "
            + ", ".join(str(value) for value in sorted(duplicate_source_file_ids))
        )

    files_by_part: dict[str, list[str]] = {}
    for drawing in drawings:
        files_by_part.setdefault(
            normalized_part_number(drawing.part_number),
            [],
        ).append(drawing.file_name)
    conflicts = {
        part_number: tuple(file_names)
        for part_number, file_names in files_by_part.items()
        if len(file_names) > 1
    }
    if conflicts:
        raise BoxDuplicatePartDrawingError(conflicts)

    return BoxMeasurementContract(
        schema=BOX_MEASUREMENT_SCHEMA,
        items=tuple(drawings),
    )


# 读取器角色 -> Excel 板件类型（与拆板 PhysicalPlateRole 成对等价对齐）：
#   腹 -> BOX腹 ×2（WEB_LEFT+WEB_RIGHT 相同）
#   上腹/下腹 -> BOX腹 ×1（各自不同）
#   翼 -> BOX翼 ×2（FLANGE_TOP+FLANGE_BOTTOM 相同）
#   上翼/下翼 -> BOX翼 ×1
_ROLE_TO_PART = {
    "腹": ("BOX腹", Decimal("2")),
    "上腹": ("BOX腹", Decimal("1")),
    "下腹": ("BOX腹", Decimal("1")),
    "翼": ("BOX翼", Decimal("2")),
    "上翼": ("BOX翼", Decimal("1")),
    "下翼": ("BOX翼", Decimal("1")),
}
_PART_ORDER = {"BOX腹": 0, "BOX翼": 1}
# 同类型内排序：合并（腹/翼）在前，上/下拆分按 上、下 顺序
_ROLE_ORDER = {"腹": 0, "上腹": 0, "下腹": 1, "翼": 0, "上翼": 0, "下翼": 1}


def map_box_role(part_number: object, role: object) -> BoxRoleMapping:
    """Map one verified Reader role to its physical Excel plate identity."""
    normalized_part = _normalized_text(part_number)
    normalized_role = _normalized_text(role)
    if not normalized_part:
        raise ValueError("BOX 零件号不能为空")
    entry = _ROLE_TO_PART.get(normalized_role)
    if entry is None:
        raise ValueError(f"无法识别的 BOX Reader 角色: {normalized_role!r}")
    part_type, multiplier = entry
    return BoxRoleMapping(
        role=normalized_role,
        part_type=part_type,
        import_part_no=f"{normalized_part}-BOX{normalized_role}",
        quantity_multiplier=multiplier,
        sort_key=(_PART_ORDER[part_type], _ROLE_ORDER[normalized_role]),
    )


def _physical_decimal(
    value: object,
    *,
    field: str,
    allow_zero: bool = False,
) -> Decimal:
    try:
        number = Decimal(str(value))
    except ArithmeticError as exc:
        raise ValueError(f"BOX {field} 不是有效数值") from exc
    if not number.is_finite():
        raise ValueError(f"BOX {field} 必须是有限数")
    if number < 0 or (number == 0 and not allow_zero):
        comparator = ">=0" if allow_zero else ">0"
        raise ValueError(f"BOX {field} 必须{comparator}")
    return number


def _validate_role_combination(mappings: list[BoxRoleMapping]) -> None:
    """组合校验：翼系 {翼} 或 {上翼,下翼}；腹系 {腹} 或 {上腹,下腹}。"""
    webs = [mapping.role for mapping in mappings if mapping.part_type == "BOX腹"]
    flanges = [mapping.role for mapping in mappings if mapping.part_type == "BOX翼"]
    web_ok = webs == ["腹"] or webs in (["上腹", "下腹"], ["下腹", "上腹"])
    flange_ok = flanges == ["翼"] or flanges in (["上翼", "下翼"], ["下翼", "上翼"])
    if not web_ok or not flange_ok:
        roles = "、".join(mapping.role for mapping in mappings)
        raise ValueError(f"BOX Reader 板件角色组合不完整: {roles}")


def build_box_plate_plans(
    *,
    part_number: object,
    model_length: object,
    material: object,
    web_spec: object,
    web_width: object,
    flange_spec: object,
    flange_width: object,
    measurements: tuple[BoxSetbackMeasurement, ...],
) -> tuple[BoxPlatePlan, ...]:
    """Build stable physical plate plans from one valid Reader drawing."""
    normalized_part = _normalized_text(part_number)
    if not normalized_part:
        raise ValueError("BOX 零件号不能为空")
    normalized_material = _normalized_text(material)
    if not normalized_material:
        raise ValueError("BOX 材质不能为空")
    length = _physical_decimal(model_length, field="原长度")
    dimensions = {
        "BOX腹": (
            _physical_decimal(web_spec, field="腹板规格"),
            _physical_decimal(web_width, field="腹板宽度"),
        ),
        "BOX翼": (
            _physical_decimal(flange_spec, field="翼板规格"),
            _physical_decimal(flange_width, field="翼板宽度"),
        ),
    }

    mapped: list[
        tuple[BoxRoleMapping, BoxSetbackMeasurement, Decimal, Decimal, Decimal]
    ] = []
    seen_roles: set[str] = set()
    for measurement in measurements:
        role_mapping = map_box_role(normalized_part, measurement.role)
        if role_mapping.role in seen_roles:
            raise ValueError(f"BOX Reader 角色重复: {role_mapping.role}")
        seen_roles.add(role_mapping.role)
        left = _physical_decimal(
            measurement.left_safe,
            field=f"{role_mapping.role}左进",
            allow_zero=True,
        )
        right = _physical_decimal(
            measurement.right_safe,
            field=f"{role_mapping.role}右进",
            allow_zero=True,
        )
        cut_length = length - left - right
        if cut_length <= 0:
            raise ValueError(
                f"BOX {role_mapping.role} 下料长度必须>0: {length}-{left}-{right}"
            )
        mapped.append((role_mapping, measurement, left, right, cut_length))
    mapped.sort(key=lambda value: value[0].sort_key)
    _validate_role_combination([value[0] for value in mapped])

    plans: list[BoxPlatePlan] = []
    for role_mapping, _measurement, left, right, cut_length in mapped:
        spec, width = dimensions[role_mapping.part_type]
        plans.append(BoxPlatePlan(
            source_roles=(role_mapping.role,),
            part_type=role_mapping.part_type,
            import_part_no=role_mapping.import_part_no,
            spec=spec,
            width=width,
            model_length=length,
            left_safe=left,
            right_safe=right,
            cut_length=cut_length,
            material=normalized_material,
            quantity_multiplier=role_mapping.quantity_multiplier,
        ))
    return tuple(plans)


_BOX_PART_TYPES = frozenset({"BOX腹", "BOX翼"})


def _source_key(row: Mapping[str, object]) -> tuple[str, int]:
    source_sheet = row.get("_source_sheet")
    source_row = row.get("_source_row")
    if not isinstance(source_sheet, str) or not isinstance(source_row, int):
        raise ValueError("BOX 基线行缺少来源sheet或来源行")
    return source_sheet, source_row


def _box_profile(value: object, *, source: str) -> FabricatedProfile:
    profile = parse_fabricated_profile(value)
    if profile is None or profile.kind != "BOX":
        raise ValueError(f"{source} 不是有效 BOX 规格: {value!r}")
    return profile


def _baseline_dimensions(
    rows: tuple[Mapping[str, object], ...],
    profile: FabricatedProfile,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    by_type: dict[str, list[Mapping[str, object]]] = {"BOX腹": [], "BOX翼": []}
    for row in rows:
        part_type = str(row.get("类型") or "")
        if part_type in by_type:
            by_type[part_type].append(row)
    if any(len(by_type[part_type]) != 1 for part_type in _BOX_PART_TYPES):
        raise ValueError("每个 BOX 来源身份必须恰好包含一条腹板和一条翼板基线")
    web, flange = profile.children()
    expected = {
        "BOX腹": (web.thickness, web.width),
        "BOX翼": (flange.thickness, flange.width),
    }
    for part_type, (spec, width) in expected.items():
        row = by_type[part_type][0]
        if row.get("规格") != spec or row.get("宽度") != width:
            raise ValueError(
                f"{part_type} 基线尺寸与 Excel BOX 截面不一致: "
                f"{row.get('规格')!r}*{row.get('宽度')!r} != {spec}*{width}"
            )
    return by_type["BOX腹"][0], by_type["BOX翼"][0]


def _enhanced_organized_row(
    baseline: Mapping[str, object],
    *,
    plan: BoxPlatePlan,
    source,
    source_file_id: int,
) -> dict[str, object]:
    quantity = source.original_qty * plan.quantity_multiplier
    total_count = quantity * source.component_qty
    theory_unit = plate_unit_weight(plan.spec, plan.width, plan.cut_length)
    row = dict(baseline)
    row.update({
        "类型": plan.part_type,
        "导入零件号": plan.import_part_no,
        "规格": plan.spec,
        "宽度": plan.width,
        "长度(mm)": plan.model_length,
        "左进(mm)": plan.left_safe,
        "右进(mm)": plan.right_safe,
        "下料长度(mm)": plan.cut_length,
        "原数量": source.original_qty,
        "数量": quantity,
        "总数": total_count,
        "总长(mm)": plan.cut_length * total_count,
        "比重": Decimal("7.85"),
        "比重来源": "plate_constant:7.85",
        "理单重(kg)": round_weight_for_output(theory_unit),
        "理总重(kg)": round_weight_for_output(theory_unit * total_count),
        "_stage2_source_file_id": source_file_id,
        "_stage2_source_roles": plan.source_roles,
        "_stage2_status": "complete",
    })
    return row


def _enhanced_part_candidate(
    *,
    plan: BoxPlatePlan,
    source,
    excluded: bool,
) -> PartCandidate:
    return PartCandidate(
        source_sheet=source.source_sheet,
        source_row=source.source_row,
        source_seq=source.source_seq,
        import_component_no=source.component_no,
        import_part_no=plan.import_part_no,
        spec=plan.spec,
        width=plan.width,
        cut_length=plan.cut_length,
        material=source.material,
        child_quantity=source.original_qty * plan.quantity_multiplier,
        component_quantity=source.component_qty,
        part_type=plan.part_type,
        team="",
        graphic="",
        excluded=excluded,
        model_length=plan.model_length,
        left_setback=plan.left_safe,
        right_setback=plan.right_safe,
    )


def _stage2_warning(
    source,
    *,
    category: str,
    field: str,
    actual: object,
    expected: object,
    description: str,
) -> QualityIssue:
    return QualityIssue(
        level=IssueLevel.WARNING,
        category=category,
        source_sheet=source.source_sheet,
        source_row=source.source_row,
        component_no=source.component_no,
        part_no=source.part_no,
        spec=source.original_spec,
        field=field,
        actual_value=actual,
        expected_value=expected,
        absolute_error=None,
        relative_error=None,
        affects_part=False,
        density_source=None,
        description=description,
    )


def _manual_placeholder_rows(
    baseline_rows: tuple[Mapping[str, object], ...],
    *,
    source_file_id: int,
) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for baseline in baseline_rows:
        row = dict(baseline)
        row.update({
            "左进(mm)": None,
            "右进(mm)": None,
            "下料长度(mm)": None,
            "总长(mm)": None,
            "理单重(kg)": None,
            "理总重(kg)": None,
            "重量核验": (
                "警告" if row.get("重量核验") == "通过" else row.get("重量核验")
            ),
            "_stage2_source_file_id": source_file_id,
            "_stage2_status": "manual",
            "_stage2_issue_category": "BOX读取失败需补录",
        })
        result.append(row)
    return tuple(result)


def enhance_box_projection(
    projection: CanonicalProjection,
    contract: BoxMeasurementContract,
) -> BoxEnhancementResult:
    """Replace valid BOX baseline rows/candidates with setback-aware plans."""
    organized = tuple(projection.organized_rows)
    box_rows_by_source: dict[
        tuple[str, int],
        list[Mapping[str, object]],
    ] = {}
    source_order: list[tuple[str, int]] = []
    for row in organized:
        if row.get("类型") not in _BOX_PART_TYPES:
            continue
        key = _source_key(row)
        if key not in box_rows_by_source:
            source_order.append(key)
        box_rows_by_source.setdefault(key, []).append(row)

    source_parts = {
        (part.source_sheet, part.source_row): part
        for part in projection.cleaned_parts
    }
    drawings_by_part = {
        normalized_part_number(drawing.part_number): drawing
        for drawing in contract.items
    }
    missing_source_keys = [key for key in source_order if key not in source_parts]
    if missing_source_keys:
        raise ValueError(
            "BOX 基线来源不存在于清洗表: "
            + "、".join(f"{sheet}!{row}" for sheet, row in missing_source_keys)
        )
    excel_part_keys = {
        normalized_part_number(source_parts[key].part_no)
        for key in source_order
    }
    unmatched_drawings = sum(
        part_key not in excel_part_keys for part_key in drawings_by_part
    )

    replacement_rows: dict[tuple[str, int], tuple[dict[str, object], ...]] = {}
    replacement_candidates: dict[
        tuple[str, int],
        tuple[PartCandidate, ...],
    ] = {}
    missing = 0
    matched = 0
    manual = 0
    issues = list(projection.issues)
    for part_key, drawing in drawings_by_part.items():
        if part_key in excel_part_keys:
            continue
        issues.append(QualityIssue(
            level=IssueLevel.WARNING,
            category="BOX图纸未进入Excel",
            source_sheet="分类账",
            source_row=drawing.source_file_id,
            component_no=None,
            part_no=drawing.part_number,
            spec=drawing.classification_spec,
            field="零件号",
            actual_value=drawing.file_name,
            expected_value="Excel第一阶段存在对应BOX零件号",
            absolute_error=None,
            relative_error=None,
            affects_part=False,
            density_source=None,
            description=(
                f"图纸 {drawing.file_name}（零件 {drawing.part_number}）"
                "在 Excel 第一阶段没有对应 BOX 零件"
            ),
        ))
    for key in source_order:
        source = source_parts.get(key)
        if source is None:
            raise ValueError(f"BOX 基线来源不存在于清洗表: {key!r}")
        baseline_rows = tuple(box_rows_by_source[key])
        excel_profile = _box_profile(source.original_spec, source="Excel")
        web_row, flange_row = _baseline_dimensions(baseline_rows, excel_profile)
        baseline_candidates = tuple(
            candidate
            for candidate in projection.part_candidates
            if (candidate.source_sheet, candidate.source_row) == key
            and candidate.part_type in _BOX_PART_TYPES
        )
        baseline_candidate_types = sorted(
            candidate.part_type for candidate in baseline_candidates
        )
        if baseline_candidate_types != sorted(_BOX_PART_TYPES):
            raise ValueError(
                "每个 BOX 来源身份必须恰好包含一条腹板和一条翼板 part候选基线"
            )
        drawing = drawings_by_part.get(normalized_part_number(source.part_no))
        if drawing is None:
            missing += 1
            replacement_rows[key] = tuple({
                **dict(row),
                "重量核验": (
                    "警告" if row.get("重量核验") == "通过" else row.get("重量核验")
                ),
                "_stage2_status": "missing",
                "_stage2_issue_category": "BOX缺图沿用原长度",
            } for row in baseline_rows)
            issues.append(_stage2_warning(
                source,
                category="BOX缺图沿用原长度",
                field="左右进",
                actual="未找到对应拆板前BOX图纸",
                expected="当前项目分类账中有且仅有一张对应BOX图纸",
                description=(
                    f"零件 {source.part_no} 未找到对应拆板前 BOX 图；"
                    "本次仍按第一阶段原长度保留"
                ),
            ))
            continue
        try:
            if drawing.status != "OK":
                raise ValueError(
                    f"Reader状态={drawing.status}; "
                    + " | ".join(drawing.warnings[:3])
                )
            classification_profile = _box_profile(
                drawing.classification_spec,
                source="分类账",
            )
            reader_profile = _box_profile(drawing.reader_spec, source="Reader")
            if not (
                classification_profile.normalized_spec
                == reader_profile.normalized_spec
                == excel_profile.normalized_spec
            ):
                raise ValueError(
                    "BOX三方规格不一致: "
                    f"分类={classification_profile.normalized_spec}, "
                    f"Reader={reader_profile.normalized_spec}, "
                    f"Excel={excel_profile.normalized_spec}"
                )
            web, flange = excel_profile.children()
            plans = build_box_plate_plans(
                part_number=source.part_no,
                model_length=source.length,
                material=source.material,
                web_spec=web.thickness,
                web_width=web.width,
                flange_spec=flange.thickness,
                flange_width=flange.width,
                measurements=drawing.measurements,
            )
        except ValueError as exc:
            replacement_rows[key] = _manual_placeholder_rows(
                baseline_rows,
                source_file_id=drawing.source_file_id,
            )
            replacement_candidates[key] = tuple(
                replace(
                    candidate,
                    cut_length=None,
                    model_length=source.length,
                    left_setback=None,
                    right_setback=None,
                )
                for candidate in baseline_candidates
            )
            issues.append(_stage2_warning(
                source,
                category="BOX读取失败需补录",
                field="左右进",
                actual=f"{drawing.file_name}: {exc}",
                expected="Reader状态OK、三方规格一致且全部板件左右进有效",
                description=(
                    f"零件 {source.part_no} 的图纸 {drawing.file_name} 无法可靠套用左右进；"
                    f"{exc}"
                ),
            ))
            matched += 1
            manual += 1
            continue
        rows: list[dict[str, object]] = []
        candidates: list[PartCandidate] = []
        baseline_excluded = any(
            candidate.excluded for candidate in baseline_candidates
        )
        for plan in plans:
            baseline = web_row if plan.part_type == "BOX腹" else flange_row
            rows.append(_enhanced_organized_row(
                baseline,
                plan=plan,
                source=source,
                source_file_id=drawing.source_file_id,
            ))
            candidates.append(_enhanced_part_candidate(
                plan=plan,
                source=source,
                excluded=baseline_excluded,
            ))
        replacement_rows[key] = tuple(rows)
        replacement_candidates[key] = tuple(candidates)
        matched += 1

    output_rows: list[Mapping[str, object]] = []
    emitted: set[tuple[str, int]] = set()
    for row in organized:
        if row.get("类型") not in _BOX_PART_TYPES:
            output_rows.append(row)
            continue
        key = _source_key(row)
        if key in emitted:
            continue
        emitted.add(key)
        replacements = replacement_rows.get(key)
        if replacements is None:
            output_rows.extend(box_rows_by_source[key])
        else:
            output_rows.extend(replacements)

    output_candidates: list[PartCandidate] = []
    emitted_candidate_sources: set[tuple[str, int]] = set()
    for candidate in projection.part_candidates:
        key = (candidate.source_sheet, candidate.source_row)
        replacements = replacement_candidates.get(key)
        if candidate.part_type not in _BOX_PART_TYPES or replacements is None:
            output_candidates.append(candidate)
            continue
        if key not in emitted_candidate_sources:
            output_candidates.extend(replacements)
            emitted_candidate_sources.add(key)

    if not source_order and not contract.items:
        status = "noop"
    elif missing or unmatched_drawings or manual:
        status = "partial"
    else:
        status = "complete"
    enhanced_projection = CanonicalProjection(
        cleaned_parts=projection.cleaned_parts,
        component_rows=projection.component_rows,
        organized_rows=tuple(
            MappingProxyType(dict(row)) for row in output_rows
        ),
        part_candidates=tuple(output_candidates),
        issues=tuple(issues),
    )
    return BoxEnhancementResult(
        projection=enhanced_projection,
        status=status,
        matched_occurrence_count=matched,
        missing_drawing_count=missing,
        unmatched_drawing_count=unmatched_drawings,
        manual_occurrence_count=manual,
    )
