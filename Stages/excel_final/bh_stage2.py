"""Pure BH setback domain rules used by Excel Final Stage 2."""

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


BH_MEASUREMENT_SCHEMA = "bh_setback_measurements/v1"


class BhMeasurementContractError(ValueError):
    """The internal Reader-to-Excel payload does not match its fixed schema."""


class BhDuplicatePartDrawingError(ValueError):
    code = "EXCEL_STAGE2_DUPLICATE_PART_DRAWING"

    def __init__(self, conflicts: Mapping[str, tuple[str, ...]]) -> None:
        self.conflicts = dict(conflicts)
        rendered = "；".join(
            f"{part_number}: {', '.join(file_names)}"
            for part_number, file_names in self.conflicts.items()
        )
        super().__init__(
            "同一 BH 零件号存在多张图纸，系统未自动选取：" + rendered
        )


@dataclass(frozen=True, slots=True)
class BhSetbackMeasurement:
    role: str
    left_safe: Decimal
    right_safe: Decimal


@dataclass(frozen=True, slots=True)
class BhDrawingMeasurement:
    source_file_id: int
    file_name: str
    part_number: str
    classification_spec: str
    reader_spec: str
    status: str
    warnings: tuple[str, ...]
    measurements: tuple[BhSetbackMeasurement, ...]


@dataclass(frozen=True, slots=True)
class BhMeasurementContract:
    schema: str
    items: tuple[BhDrawingMeasurement, ...]


@dataclass(frozen=True, slots=True)
class BhPlatePlan:
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
class BhEnhancementResult:
    projection: CanonicalProjection
    status: str
    matched_occurrence_count: int
    missing_drawing_count: int
    unmatched_drawing_count: int
    manual_occurrence_count: int


@dataclass(frozen=True, slots=True)
class BhRoleMapping:
    role: str
    part_type: str
    import_part_no: str
    quantity_multiplier: Decimal
    sort_key: tuple[int, int, int]


_WING_ROLE = re.compile(r"(?P<family>翼|上翼|下翼)(?:-(?P<index>\d+))?")
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
        raise BhMeasurementContractError(f"{path} 必须是对象")
    actual = frozenset(value.keys())
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected, key=str)
        raise BhMeasurementContractError(
            f"{path} 字段不符合 {BH_MEASUREMENT_SCHEMA}: "
            f"缺少={missing!r}, 未知={unknown!r}"
        )
    return value


def _required_text(value: object, *, path: str, allow_blank: bool = False) -> str:
    if not isinstance(value, str):
        raise BhMeasurementContractError(f"{path} 必须是字符串")
    normalized = _normalized_text(value)
    if not normalized and not allow_blank:
        raise BhMeasurementContractError(f"{path} 不能为空")
    return normalized


def _decimal(value: object, *, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise BhMeasurementContractError(f"{path} 必须是数值")
    try:
        return Decimal(str(value))
    except ArithmeticError as exc:
        raise BhMeasurementContractError(f"{path} 不是可解析数值") from exc


def parse_bh_measurement_contract(payload: object) -> BhMeasurementContract:
    """Parse the compact, versioned Reader result used by Stage 2."""
    root = _exact_fields(payload, _TOP_LEVEL_FIELDS, path="BH左右进合同")
    if root["schema"] != BH_MEASUREMENT_SCHEMA:
        raise BhMeasurementContractError(
            f"BH左右进合同版本必须是 {BH_MEASUREMENT_SCHEMA!r}"
        )
    raw_items = root["items"]
    if not isinstance(raw_items, list):
        raise BhMeasurementContractError("BH左右进合同.items 必须是数组")

    drawings: list[BhDrawingMeasurement] = []
    for item_index, raw_item in enumerate(raw_items):
        path = f"BH左右进合同.items[{item_index}]"
        item = _exact_fields(raw_item, _DRAWING_FIELDS, path=path)
        source_file_id = item["source_file_id"]
        if (
            isinstance(source_file_id, bool)
            or not isinstance(source_file_id, int)
            or source_file_id <= 0
        ):
            raise BhMeasurementContractError(f"{path}.source_file_id 必须是正整数")
        warnings = item["warnings"]
        if not isinstance(warnings, list) or not all(
            isinstance(warning, str) for warning in warnings
        ):
            raise BhMeasurementContractError(f"{path}.warnings 必须是字符串数组")
        raw_measurements = item["measurements"]
        if not isinstance(raw_measurements, list):
            raise BhMeasurementContractError(f"{path}.measurements 必须是数组")
        measurements: list[BhSetbackMeasurement] = []
        for measurement_index, raw_measurement in enumerate(raw_measurements):
            measurement_path = f"{path}.measurements[{measurement_index}]"
            measurement = _exact_fields(
                raw_measurement,
                _MEASUREMENT_FIELDS,
                path=measurement_path,
            )
            measurements.append(BhSetbackMeasurement(
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
        drawings.append(BhDrawingMeasurement(
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
        raise BhMeasurementContractError(
            "BH左右进合同 source_file_id 重复: "
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
        raise BhDuplicatePartDrawingError(conflicts)

    return BhMeasurementContract(
        schema=BH_MEASUREMENT_SCHEMA,
        items=tuple(drawings),
    )


def map_bh_role(part_number: object, role: object) -> BhRoleMapping:
    """Map one verified Reader role to its physical Excel plate identity."""
    normalized_part = _normalized_text(part_number)
    normalized_role = _normalized_text(role)
    if not normalized_part:
        raise ValueError("BH 零件号不能为空")
    if normalized_role == "腹":
        return BhRoleMapping(
            role=normalized_role,
            part_type="BH腹",
            import_part_no=f"{normalized_part}-BH腹",
            quantity_multiplier=Decimal("1"),
            sort_key=(0, 0, 0),
        )

    match = _WING_ROLE.fullmatch(normalized_role)
    index = int(match.group("index") or 0) if match is not None else 0
    if match is None or (match.group("index") is not None and index <= 0):
        raise ValueError(f"无法识别的 BH Reader 角色: {normalized_role!r}")
    family = match.group("family")
    family_order = {"翼": 0, "上翼": 1, "下翼": 2}[family]
    multiplier = Decimal("2") if family == "翼" else Decimal("1")
    return BhRoleMapping(
        role=normalized_role,
        part_type="BH翼",
        import_part_no=f"{normalized_part}-BH{normalized_role}",
        quantity_multiplier=multiplier,
        sort_key=(1, family_order, index),
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
        raise ValueError(f"BH {field} 不是有效数值") from exc
    if not number.is_finite():
        raise ValueError(f"BH {field} 必须是有限数")
    if number < 0 or (number == 0 and not allow_zero):
        comparator = ">=0" if allow_zero else ">0"
        raise ValueError(f"BH {field} 必须{comparator}")
    return number


def _merged_wing_part_number(
    part_number: str,
    mappings: tuple[BhRoleMapping, ...],
) -> str:
    if len(mappings) == 1:
        return mappings[0].import_part_no
    roles = tuple(mapping.role for mapping in mappings)
    if len(roles) == 2:
        upper, lower = roles
        upper_match = _WING_ROLE.fullmatch(upper)
        lower_match = _WING_ROLE.fullmatch(lower)
        if (
            upper_match is not None
            and lower_match is not None
            and upper_match.group("family") == "上翼"
            and lower_match.group("family") == "下翼"
            and upper_match.group("index") == lower_match.group("index")
        ):
            suffix = (
                f"-{upper_match.group('index')}"
                if upper_match.group("index") is not None
                else ""
            )
            return f"{part_number}-BH翼{suffix}"
    return f"{part_number}-BH" + "+".join(roles)


def _is_complete_role_index_set(indices: list[int]) -> bool:
    ordered = sorted(indices)
    return ordered == [0] or ordered == list(range(1, len(ordered) + 1))


def _validate_wing_role_combination(mappings: list[BhRoleMapping]) -> None:
    wing_indices: dict[int, list[int]] = {0: [], 1: [], 2: []}
    for mapping in mappings:
        if mapping.part_type == "BH翼":
            wing_indices[mapping.sort_key[1]].append(mapping.sort_key[2])
    combined = wing_indices[0]
    upper = wing_indices[1]
    lower = wing_indices[2]
    valid = False
    if combined:
        valid = not upper and not lower and _is_complete_role_index_set(combined)
    elif upper or lower:
        valid = (
            sorted(upper) == sorted(lower)
            and _is_complete_role_index_set(upper)
        )
    if not valid:
        roles = "、".join(mapping.role for mapping in mappings)
        raise ValueError(f"BH Reader 翼板角色组合不完整: {roles}")


def build_bh_plate_plans(
    *,
    part_number: object,
    model_length: object,
    material: object,
    web_spec: object,
    web_width: object,
    flange_spec: object,
    flange_width: object,
    measurements: tuple[BhSetbackMeasurement, ...],
) -> tuple[BhPlatePlan, ...]:
    """Build stable physical plate plans from one valid Reader drawing."""
    normalized_part = _normalized_text(part_number)
    if not normalized_part:
        raise ValueError("BH 零件号不能为空")
    normalized_material = _normalized_text(material)
    if not normalized_material:
        raise ValueError("BH 材质不能为空")
    length = _physical_decimal(model_length, field="原长度")
    dimensions = {
        "BH腹": (
            _physical_decimal(web_spec, field="腹板规格"),
            _physical_decimal(web_width, field="腹板宽度"),
        ),
        "BH翼": (
            _physical_decimal(flange_spec, field="翼板规格"),
            _physical_decimal(flange_width, field="翼板宽度"),
        ),
    }

    mapped_measurements: list[
        tuple[BhRoleMapping, BhSetbackMeasurement, Decimal, Decimal, Decimal]
    ] = []
    seen_roles: set[str] = set()
    for measurement in measurements:
        mapped = map_bh_role(normalized_part, measurement.role)
        if mapped.role in seen_roles:
            raise ValueError(f"BH Reader 角色重复: {mapped.role}")
        seen_roles.add(mapped.role)
        left = _physical_decimal(
            measurement.left_safe,
            field=f"{mapped.role}左进",
            allow_zero=True,
        )
        right = _physical_decimal(
            measurement.right_safe,
            field=f"{mapped.role}右进",
            allow_zero=True,
        )
        cut_length = length - left - right
        if cut_length <= 0:
            raise ValueError(
                f"BH {mapped.role} 下料长度必须>0: {length}-{left}-{right}"
            )
        mapped_measurements.append((mapped, measurement, left, right, cut_length))
    mapped_measurements.sort(key=lambda value: value[0].sort_key)
    if sum(mapped.part_type == "BH腹" for mapped, *_ in mapped_measurements) != 1:
        raise ValueError("BH Reader 结果必须且只能包含一个腹板角色")
    if not any(mapped.part_type == "BH翼" for mapped, *_ in mapped_measurements):
        raise ValueError("BH Reader 结果缺少翼板角色")
    _validate_wing_role_combination([
        mapped for mapped, *_ in mapped_measurements
    ])

    web_plan: BhPlatePlan | None = None
    wing_groups: dict[
        tuple[object, ...],
        list[tuple[BhRoleMapping, Decimal, Decimal, Decimal]],
    ] = {}
    for mapped, _measurement, left, right, cut_length in mapped_measurements:
        spec, width = dimensions[mapped.part_type]
        if mapped.part_type == "BH腹":
            web_plan = BhPlatePlan(
                source_roles=(mapped.role,),
                part_type=mapped.part_type,
                import_part_no=mapped.import_part_no,
                spec=spec,
                width=width,
                model_length=length,
                left_safe=left,
                right_safe=right,
                cut_length=cut_length,
                material=normalized_material,
                quantity_multiplier=mapped.quantity_multiplier,
            )
            continue
        key = (spec, width, length, left, right, normalized_material)
        wing_groups.setdefault(key, []).append((mapped, left, right, cut_length))

    assert web_plan is not None
    wing_plans: list[BhPlatePlan] = []
    for key, group in wing_groups.items():
        mappings = tuple(item[0] for item in group)
        spec, width, grouped_length, left, right, grouped_material = key
        wing_plans.append(BhPlatePlan(
            source_roles=tuple(mapping.role for mapping in mappings),
            part_type="BH翼",
            import_part_no=_merged_wing_part_number(normalized_part, mappings),
            spec=spec,
            width=width,
            model_length=grouped_length,
            left_safe=left,
            right_safe=right,
            cut_length=group[0][3],
            material=grouped_material,
            quantity_multiplier=sum(
                (mapping.quantity_multiplier for mapping in mappings),
                start=Decimal("0"),
            ),
        ))
    return (web_plan, *wing_plans)


_BH_PART_TYPES = frozenset({"BH腹", "BH翼"})


def _source_key(row: Mapping[str, object]) -> tuple[str, int]:
    source_sheet = row.get("_source_sheet")
    source_row = row.get("_source_row")
    if not isinstance(source_sheet, str) or not isinstance(source_row, int):
        raise ValueError("BH 基线行缺少来源sheet或来源行")
    return source_sheet, source_row


def _bh_profile(value: object, *, source: str) -> FabricatedProfile:
    profile = parse_fabricated_profile(value)
    if profile is None or profile.kind != "BH":
        raise ValueError(f"{source} 不是有效 BH 规格: {value!r}")
    return profile


def _baseline_dimensions(
    rows: tuple[Mapping[str, object], ...],
    profile: FabricatedProfile,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    by_type: dict[str, list[Mapping[str, object]]] = {"BH腹": [], "BH翼": []}
    for row in rows:
        part_type = str(row.get("类型") or "")
        if part_type in by_type:
            by_type[part_type].append(row)
    if any(len(by_type[part_type]) != 1 for part_type in _BH_PART_TYPES):
        raise ValueError("每个 BH 来源身份必须恰好包含一条腹板和一条翼板基线")
    web, flange = profile.children()
    expected = {
        "BH腹": (web.thickness, web.width),
        "BH翼": (flange.thickness, flange.width),
    }
    for part_type, (spec, width) in expected.items():
        row = by_type[part_type][0]
        if row.get("规格") != spec or row.get("宽度") != width:
            raise ValueError(
                f"{part_type} 基线尺寸与 Excel BH 截面不一致: "
                f"{row.get('规格')!r}*{row.get('宽度')!r} != {spec}*{width}"
            )
    return by_type["BH腹"][0], by_type["BH翼"][0]


def _enhanced_organized_row(
    baseline: Mapping[str, object],
    *,
    plan: BhPlatePlan,
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
    plan: BhPlatePlan,
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
            "_stage2_issue_category": "BH读取失败需补录",
        })
        result.append(row)
    return tuple(result)


def enhance_bh_projection(
    projection: CanonicalProjection,
    contract: BhMeasurementContract,
) -> BhEnhancementResult:
    """Replace valid BH baseline rows/candidates with setback-aware plans."""
    organized = tuple(projection.organized_rows)
    bh_rows_by_source: dict[
        tuple[str, int],
        list[Mapping[str, object]],
    ] = {}
    source_order: list[tuple[str, int]] = []
    for row in organized:
        if row.get("类型") not in _BH_PART_TYPES:
            continue
        key = _source_key(row)
        if key not in bh_rows_by_source:
            source_order.append(key)
        bh_rows_by_source.setdefault(key, []).append(row)

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
            "BH 基线来源不存在于清洗表: "
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
            category="BH图纸未进入Excel",
            source_sheet="分类账",
            source_row=drawing.source_file_id,
            component_no=None,
            part_no=drawing.part_number,
            spec=drawing.classification_spec,
            field="零件号",
            actual_value=drawing.file_name,
            expected_value="Excel第一阶段存在对应BH零件号",
            absolute_error=None,
            relative_error=None,
            affects_part=False,
            density_source=None,
            description=(
                f"图纸 {drawing.file_name}（零件 {drawing.part_number}）"
                "在 Excel 第一阶段没有对应 BH 零件"
            ),
        ))
    for key in source_order:
        source = source_parts.get(key)
        if source is None:
            raise ValueError(f"BH 基线来源不存在于清洗表: {key!r}")
        baseline_rows = tuple(bh_rows_by_source[key])
        excel_profile = _bh_profile(source.original_spec, source="Excel")
        web_row, flange_row = _baseline_dimensions(baseline_rows, excel_profile)
        baseline_candidates = tuple(
            candidate
            for candidate in projection.part_candidates
            if (candidate.source_sheet, candidate.source_row) == key
            and candidate.part_type in _BH_PART_TYPES
        )
        baseline_candidate_types = sorted(
            candidate.part_type for candidate in baseline_candidates
        )
        if baseline_candidate_types != sorted(_BH_PART_TYPES):
            raise ValueError(
                "每个 BH 来源身份必须恰好包含一条腹板和一条翼板 part候选基线"
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
                "_stage2_issue_category": "BH缺图沿用原长度",
            } for row in baseline_rows)
            issues.append(_stage2_warning(
                source,
                category="BH缺图沿用原长度",
                field="左右进",
                actual="未找到对应拆板前BH图纸",
                expected="当前项目分类账中有且仅有一张对应BH图纸",
                description=(
                    f"零件 {source.part_no} 未找到对应拆板前 BH 图；"
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
            classification_profile = _bh_profile(
                drawing.classification_spec,
                source="分类账",
            )
            reader_profile = _bh_profile(drawing.reader_spec, source="Reader")
            if not (
                classification_profile.normalized_spec
                == reader_profile.normalized_spec
                == excel_profile.normalized_spec
            ):
                raise ValueError(
                    "BH三方规格不一致: "
                    f"分类={classification_profile.normalized_spec}, "
                    f"Reader={reader_profile.normalized_spec}, "
                    f"Excel={excel_profile.normalized_spec}"
                )
            web, flange = excel_profile.children()
            plans = build_bh_plate_plans(
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
                category="BH读取失败需补录",
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
            baseline = web_row if plan.part_type == "BH腹" else flange_row
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
        if row.get("类型") not in _BH_PART_TYPES:
            output_rows.append(row)
            continue
        key = _source_key(row)
        if key in emitted:
            continue
        emitted.add(key)
        replacements = replacement_rows.get(key)
        if replacements is None:
            output_rows.extend(bh_rows_by_source[key])
        else:
            output_rows.extend(replacements)

    output_candidates: list[PartCandidate] = []
    emitted_candidate_sources: set[tuple[str, int]] = set()
    for candidate in projection.part_candidates:
        key = (candidate.source_sheet, candidate.source_row)
        replacements = replacement_candidates.get(key)
        if candidate.part_type not in _BH_PART_TYPES or replacements is None:
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
    return BhEnhancementResult(
        projection=enhanced_projection,
        status=status,
        matched_occurrence_count=matched,
        missing_drawing_count=missing,
        unmatched_drawing_count=unmatched_drawings,
        manual_occurrence_count=manual,
    )
