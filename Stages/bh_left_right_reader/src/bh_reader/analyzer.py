from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import exp, floor, hypot
from pathlib import Path
import re
from statistics import median
from typing import Iterable, Sequence

from .model import (
    BHSpec,
    DrawingData,
    DrawingResult,
    LocalSegment,
    PlateMeasurement,
    Primitive,
    ViewCandidate,
)

SPEC_RE = re.compile(
    r"\bB?H\s*(\d+(?:\.\d+)?)"
    r"(?:\s*-\s*(\d+(?:\.\d+)?))?"
    r"\s*[*xX×]\s*(\d+(?:\.\d+)?)"
    r"\s*[*xX×]\s*(\d+(?:\.\d+)?)"
    r"\s*[*xX×]\s*(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
PART_MARK_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}$")
NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
SCALE_RE = re.compile(r"^\s*1\s*[:：]\s*(\d+(?:\.\d+)?)\s*$")


@dataclass(slots=True)
class AnalyzerConfig:
    endpoint_tolerance_mm: float = 0.20
    layer_name_regex: str = r"(?i)(^part$|part(?!mark)|profile|outline|contour|构件|零件轮廓)"
    minimum_view_segments: int = 4
    spec_dimension_tolerance_ratio: float = 0.12
    same_flange_tolerance_mm: float = 0.50
    web_boundary_min_span_ratio: float = 0.22
    web_boundary_min_steepness_ratio: float = 0.75
    web_end_window_ratio: float = 0.10
    web_end_window_min_mm: float = 250.0
    minimum_confidence_to_emit: float = 0.72
    trace_target_samples: int = 2200
    trace_min_step_mm: float = 1.0
    trace_max_step_mm: float = 5.0
    flange_pair_projection_tolerance_ratio: float = 0.20
    flange_gap_bridge_ratio: float = 0.01
    flange_gap_bridge_thickness_factor: float = 3.0
    flange_min_piece_ratio: float = 0.03
    flange_min_piece_thickness_factor: float = 4.0
    four_flange_gap_overlap_ratio: float = 0.60
    unit_verification_tolerance_ratio: float = 0.02
    numerical_floor_epsilon_mm: float = 0.0
    dimension_corroboration_tolerance_mm: float = 0.50
    flange_anchor_depth_error_ratio: float = 0.16
    flange_track_assignment_depth_ratio: float = 0.06
    flange_track_assignment_thickness_factor: float = 2.5
    flange_track_fit_min_anchors: int = 5
    flange_shape_delta_tolerance_mm: float = 5.0
    flange_shape_delta_tolerance_depth_ratio: float = 0.05
    flange_profile_shape_samples: int = 17
    flange_end_alignment_step_factor: float = 2.5
    flange_end_alignment_thickness_factor: float = 0.75
    unit_contradiction_tolerance_ratio: float = 0.25
    header_unit_match_tolerance_ratio: float = 1e-9
    header_unit_score_bonus: float = 1.50


@dataclass(slots=True)
class _Pair:
    center: float
    low: float
    high: float
    segment_ids: tuple[int, ...]


@dataclass(slots=True)
class _CrossSection:
    s: float
    lower: _Pair | None
    upper: _Pair | None


@dataclass(slots=True)
class _FlangePiece:
    index: int
    left: float
    right: float
    occupancy_run: tuple[float, float]
    segment_ids: set[int]
    center_start: float | None
    center_end: float | None
    center_profile: tuple[tuple[float, float], ...]
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _FlangeTrace:
    side: str
    left: float
    right: float
    piece_count: int
    runs: list[tuple[float, float]]
    pieces: list[_FlangePiece]
    seam_positions: list[float]
    selected_segment_ids: set[int]
    inner_values: list[float]
    outer_values: list[float]
    sample_step: float
    center_start: float | None = None
    center_end: float | None = None
    evidence: list[str] = field(default_factory=list)


class BHAnalyzer:
    """BH 左右进读取器的核心分析器（固定三步流程）。

    安全语义（fail-closed）：长度严格向下取整防下料偏短；单位先统一到 mm
    再验证；几何无法区分时拒绝输出或保守齐平，绝不外推大进尺。输出低于
    ``minimum_confidence_to_emit`` 时被拒绝。回归基线见 README 的 199 张
    回归图结论。
    """

    def __init__(self, config: AnalyzerConfig | None = None):
        self.config = config or AnalyzerConfig()
        self._layer_re = re.compile(self.config.layer_name_regex)

    def _step1_locate_main_view(
        self, drawing: DrawingData, spec: BHSpec | None
    ) -> tuple[ViewCandidate | None, ViewCandidate | None, list[str]]:
        """Step 1: locate the longitudinal view and its horizontal X bounds."""
        warnings: list[str] = []
        unit_hint = drawing.header_unit_to_mm
        candidates = self._view_candidates(
            drawing.primitives,
            strict_layers=True,
            spec=spec,
            coordinate_unit_to_mm_hint=unit_hint,
        )
        if not candidates:
            warnings.append("未发现标准 Part 轮廓层，启用宽松几何层回退")
            candidates = self._view_candidates(
                drawing.primitives,
                strict_layers=False,
                spec=spec,
                coordinate_unit_to_mm_hint=unit_hint,
            )
        front, top, view_warnings = self._select_views(
            candidates, spec, declared_unit_to_mm=unit_hint
        )
        warnings.extend(view_warnings)
        return front, top, warnings

    def _step2_identify_three_plates(
        self, front: ViewCandidate, spec: BHSpec
    ) -> tuple[
        _FlangeTrace,
        _FlangeTrace,
        tuple[float, float, str, float] | None,
        list[str],
    ]:
        """Step 2: identify the web and every physical upper/lower flange plate.

        The business process still has three semantic groups—web, upper flange,
        lower flange—but either flange group may contain more than one physical
        plate.  Plate quantity is determined only from X-ranges where the full
        flange thickness is materially present.  Internal lines never split a
        plate.
        """
        lower_trace, upper_trace, warnings = self._trace_flange_profiles(front, spec)
        web_geometry = self._web_extents(front, lower_trace, upper_trace, spec)
        return lower_trace, upper_trace, web_geometry, warnings

    def _step3_read_horizontal_setbacks(
        self,
        front: ViewCandidate,
        top: ViewCandidate | None,
        spec: BHSpec,
        lower_trace: _FlangeTrace,
        upper_trace: _FlangeTrace,
        web_geometry: tuple[float, float, str, float],
        dimension_values: Sequence[float],
    ) -> tuple[list[PlateMeasurement], float, list[str]]:
        """Step 3: read left/right setbacks as global horizontal X differences."""
        return self._measure(
            front, top, spec, lower_trace, upper_trace, web_geometry, dimension_values
        )

    def analyze(self, drawing: DrawingData) -> DrawingResult:
        spec = self._extract_spec(drawing.texts)
        part_number = self._extract_part_number(drawing)
        warnings = list(drawing.audit_messages)
        if drawing.fatal_messages:
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text if spec else "",
                "ERROR_DXF_PARSE_INCOMPLETE",
                0.0,
                [],
                warnings + drawing.fatal_messages,
            )
        unsupported_material = [
            item for item in drawing.unsupported_geometry
            if self._is_part_layer(item.layer)
        ]
        if unsupported_material:
            warnings.extend(
                f"unsupported material-layer {item.kind} geometry in "
                f"{item.source_block or 'MODELSPACE'}: {item.reason or 'no safe source-edge reader'}"
                for item in unsupported_material
            )
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text if spec else "",
                "ERROR_DXF_PARSE_INCOMPLETE",
                0.0,
                [],
                warnings,
                diagnostics={
                    "unsupported_source_entities": self._unsupported_diagnostics(drawing)
                },
            )

        front, top, view_warnings = self._step1_locate_main_view(drawing, spec)
        warnings.extend(view_warnings)
        used_broad_fallback = any("宽松几何层回退" in item for item in view_warnings)
        if used_broad_fallback and drawing.unsupported_geometry:
            warnings.extend(
                f"unsupported fallback-view {item.kind} geometry in "
                f"{item.source_block or 'MODELSPACE'} on layer {item.layer}: "
                f"{item.reason or 'no safe source-edge reader'}"
                for item in drawing.unsupported_geometry
            )
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text if spec else "",
                "ERROR_DXF_PARSE_INCOMPLETE",
                0.0,
                [],
                warnings,
                diagnostics={
                    "unsupported_source_entities": self._unsupported_diagnostics(drawing),
                    "view_selection": {"used_broad_geometry_fallback": True},
                },
            )
        if front is None:
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text if spec else "",
                "ERROR_VIEW_NOT_FOUND",
                0.0,
                [],
                warnings,
            )

        if spec is None:
            return DrawingResult(
                drawing.path.name,
                part_number,
                "",
                "ERROR_BH_SPEC_NOT_FOUND",
                0.0,
                [],
                warnings + ["未识别到 BH 截面规格，禁止推测翼厚和腹厚"],
                diagnostics=self._diagnostics(front, top, spec, drawing),
            )

        unit_diagnostics = self._unit_diagnostics(front, spec, drawing)
        if unit_diagnostics.get("status") != "verified_mm":
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text,
                "ERROR_UNIT_SCALE_UNVERIFIED",
                0.0,
                [],
                warnings + [
                    "DXF坐标到毫米的比例无法由标题栏长度或BH截面尺寸可靠验证；"
                    "为防止等比例缩放导致错误，已停止输出左右进"
                ],
                diagnostics=self._diagnostics(front, top, spec, drawing),
            )

        try:
            lower_trace, upper_trace, web_geometry, trace_warnings = (
                self._step2_identify_three_plates(front, spec)
            )
        except ValueError as exc:
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text,
                "ERROR_FLANGE_IDENTIFICATION",
                0.0,
                [],
                warnings + [str(exc)],
                diagnostics=self._diagnostics(front, top, spec, drawing),
            )
        warnings.extend(trace_warnings)

        flange_diag = {
            "lower": self._trace_diagnostics(lower_trace, front),
            "upper": self._trace_diagnostics(upper_trace, front),
        }
        diagnostics = self._diagnostics(front, top, spec, drawing)
        diagnostics["flange_analysis"] = flange_diag
        diagnostics["plate_identification"] = {
            "upper_flange_count": upper_trace.piece_count,
            "lower_flange_count": lower_trace.piece_count,
            "total_flange_count": lower_trace.piece_count + upper_trace.piece_count,
            "web": None
            if web_geometry is None
            else {
                "left_x_dxf": web_geometry[0],
                "right_x_dxf": web_geometry[1],
                "left_offset_mm": max(0.0, (web_geometry[0] - front.s_min) * front.unit_scale_to_mm),
                "right_offset_mm": max(0.0, (front.s_max - web_geometry[1]) * front.unit_scale_to_mm),
                "evidence": web_geometry[2],
                "confidence": web_geometry[3],
            },
        }

        multi_aligned, multi_check = self._four_flange_split_alignment(
            lower_trace, upper_trace, front.unit_scale_to_mm, front.s_min
        )
        lower_plate_count = lower_trace.piece_count
        upper_plate_count = upper_trace.piece_count
        total_flange_plates = lower_plate_count + upper_plate_count
        diagnostics["plate_identification"]["upper_flange_count"] = upper_plate_count
        diagnostics["plate_identification"]["lower_flange_count"] = lower_plate_count
        diagnostics["plate_identification"]["total_flange_count"] = total_flange_plates
        diagnostics["plate_identification"]["upper_flange_pieces"] = [
            self._piece_diagnostics(piece, front) for piece in upper_trace.pieces
        ]
        diagnostics["plate_identification"]["lower_flange_pieces"] = [
            self._piece_diagnostics(piece, front) for piece in lower_trace.pieces
        ]
        diagnostics["multi_flange_check"] = multi_check
        diagnostics["four_flange_check"] = multi_check  # backward-compatible diagnostic key

        if lower_plate_count > 1 or upper_plate_count > 1:
            warnings.append(
                f"识别到上翼 {upper_plate_count} 块、下翼 {lower_plate_count} 块；"
                "已按实体连续区间从左到右编号并逐块读取左右进，不再跳过"
            )
        if multi_aligned and lower_plate_count == 2 and upper_plate_count == 2:
            warnings.append(
                "上翼和下翼均在构件中部形成两个独立实体区间；按上翼-1/2、下翼-1/2输出"
            )

        if web_geometry is None:
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text,
                "ERROR_WEB_IDENTIFICATION",
                0.0,
                [],
                warnings + ["未能在第二步稳定识别腹板水平边界"],
                diagnostics=diagnostics,
            )

        try:
            measurements, confidence, measure_warnings = self._step3_read_horizontal_setbacks(
                front,
                top,
                spec,
                lower_trace,
                upper_trace,
                web_geometry,
                self._dimension_values(drawing.texts),
            )
        except ValueError as exc:
            warnings.append(str(exc))
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text,
                "ERROR_GEOMETRY",
                0.0,
                [],
                warnings,
                diagnostics=diagnostics,
            )

        warnings.extend(measure_warnings)
        status = "OK" if confidence >= self.config.minimum_confidence_to_emit else "REVIEW_LOW_CONFIDENCE"
        if status != "OK":
            warnings.append("置信度低于生产输出阈值，必须人工复核后才能下料")
        return DrawingResult(
            drawing.path.name,
            part_number,
            spec.raw_text,
            status,
            confidence,
            measurements,
            warnings,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _extract_spec(texts: Iterable[Primitive]) -> BHSpec | None:
        items = list(texts)
        for item in items:
            normalized = item.text.replace(" ", "")
            match = SPEC_RE.search(normalized)
            if not match:
                continue
            depth = float(match.group(1))
            depth_end = float(match.group(2)) if match.group(2) else None
            width = float(match.group(3))
            web = float(match.group(4))
            flange = float(match.group(5))
            nominal_length = BHAnalyzer._nearest_title_length(item, items)
            drawing_scale_text, drawing_scale_denominator = BHAnalyzer._nearest_title_scale(item, items)
            return BHSpec(
                depth=depth,
                width=width,
                web_thickness=web,
                flange_thickness=flange,
                raw_text=match.group(0),
                depth_end=depth_end,
                nominal_length=nominal_length,
                drawing_scale_text=drawing_scale_text,
                drawing_scale_denominator=drawing_scale_denominator,
            )
        return None

    @staticmethod
    def _nearest_title_length(spec_text: Primitive, texts: Sequence[Primitive]) -> float | None:
        if not spec_text.points:
            return None
        sx, sy = spec_text.points[0]
        candidates: list[tuple[float, float]] = []
        for item in texts:
            if item is spec_text or item.source_block != spec_text.source_block or not item.points:
                continue
            value = item.text.strip()
            if not NUMBER_RE.fullmatch(value):
                continue
            x, y = item.points[0]
            if x <= sx or abs(y - sy) > 12.0:
                continue
            number = float(value)
            if number < 100.0:
                continue
            candidates.append((x - sx, number))
        return min(candidates, default=(0.0, None), key=lambda item: item[0])[1]

    @staticmethod
    def _nearest_title_scale(
        spec_text: Primitive, texts: Sequence[Primitive]
    ) -> tuple[str | None, float | None]:
        """Read title-block paper scale; never use it as coordinate conversion."""
        if not spec_text.points:
            return None, None
        sx, sy = spec_text.points[0]
        candidates: list[tuple[float, str, float]] = []
        for item in texts:
            if item is spec_text or item.source_block != spec_text.source_block or not item.points:
                continue
            match = SCALE_RE.fullmatch(item.text.strip())
            if not match:
                continue
            x, y = item.points[0]
            if x <= sx or abs(y - sy) > 20.0:
                continue
            denominator = float(match.group(1))
            candidates.append((x - sx, item.text.strip(), denominator))
        if not candidates:
            return None, None
        _, text, denominator = min(candidates, key=lambda item: item[0])
        return text, denominator

    @staticmethod
    def _extract_part_number(drawing: DrawingData) -> str:
        stem = drawing.path.stem
        cleaned_stem = re.sub(r"_?拆板前.*$", "", stem, flags=re.IGNORECASE)
        part_mark_candidates = [
            item.text.strip()
            for item in drawing.texts
            if "partmark" in item.layer.lower() and PART_MARK_RE.fullmatch(item.text.strip())
        ]
        if cleaned_stem in part_mark_candidates:
            return cleaned_stem
        if part_mark_candidates:
            return min(part_mark_candidates, key=len)
        text_candidates = [
            item.text.strip()
            for item in drawing.texts
            if PART_MARK_RE.fullmatch(item.text.strip())
        ]
        if cleaned_stem in text_candidates:
            return cleaned_stem
        if text_candidates:
            prefix_matches = [value for value in text_candidates if stem.startswith(value)]
            if prefix_matches:
                return max(prefix_matches, key=len)
            return min(text_candidates, key=len)
        return cleaned_stem or stem

    def _is_part_layer(self, layer: str) -> bool:
        normalized = layer.strip()
        if re.search(r"(?i)partmark|bolt|dimension|dim|mark|sheet|text|annotation", normalized):
            return False
        return bool(self._layer_re.search(normalized))

    def _view_candidates(
        self,
        primitives: Iterable[Primitive],
        strict_layers: bool = True,
        spec: BHSpec | None = None,
        coordinate_unit_to_mm_hint: float | None = None,
    ) -> list[ViewCandidate]:
        grouped: dict[str, list[tuple[tuple[float, float], tuple[float, float], Primitive]]] = defaultdict(list)
        fallback: list[tuple[tuple[float, float], tuple[float, float], Primitive]] = []
        unit_hint = (
            coordinate_unit_to_mm_hint
            if coordinate_unit_to_mm_hint is not None and coordinate_unit_to_mm_hint > 0
            else 1.0
        )
        endpoint_tolerance_units = self.config.endpoint_tolerance_mm / unit_hint
        for primitive in primitives:
            if len(primitive.points) < 2:
                continue
            if strict_layers:
                if not self._is_part_layer(primitive.layer):
                    continue
            elif re.search(
                r"(?i)partmark|bolt|dimension|dim|mark|sheet|text|annotation|hatch|section",
                primitive.layer,
            ):
                continue
            for a, b in zip(primitive.points, primitive.points[1:], strict=False):
                if hypot(b[0] - a[0], b[1] - a[1]) <= endpoint_tolerance_units:
                    continue
                item = (a, b, primitive)
                grouped[primitive.source_block or "MODELSPACE"].append(item)
                fallback.append(item)

        groups = [items for items in grouped.values() if len(items) >= self.config.minimum_view_segments]
        if not groups and fallback:
            groups = [fallback]

        result: list[ViewCandidate] = []
        for index, items in enumerate(groups):
            # Business rule: left/right setback is always horizontal global X.
            axis = (1.0, 0.0)
            normal = (0.0, 1.0)
            local_segments: list[LocalSegment] = []
            x_values: list[float] = []
            y_values: list[float] = []
            for a, b, primitive in items:
                local_segments.append(
                    LocalSegment(a, b, primitive.layer, primitive.source_block, primitive.source_handle)
                )
                x_values.extend((a[0], b[0]))
                y_values.extend((a[1], b[1]))
            if not x_values:
                continue
            result.append(
                ViewCandidate(
                    view_id=items[0][2].source_block or f"view-{index + 1}",
                    segments=local_segments,
                    axis=axis,
                    normal=normal,
                    s_min=min(x_values),
                    s_max=max(x_values),
                    t_min=min(y_values),
                    t_max=max(y_values),
                )
            )
        return result

    @staticmethod
    def _merge_intervals(intervals: list[tuple[float, float]], tolerance: float = 0.5) -> list[tuple[float, float]]:
        if not intervals:
            return []
        ordered = sorted((min(a, b), max(a, b)) for a, b in intervals)
        merged = [ordered[0]]
        for start, end in ordered[1:]:
            old_start, old_end = merged[-1]
            if start <= old_end + tolerance:
                merged[-1] = (old_start, max(old_end, end))
            else:
                merged.append((start, end))
        return merged

    def _longitudinal_levels(
        self,
        view: ViewCandidate,
        scale_to_mm: float = 1.0,
    ) -> list[tuple[float, list[tuple[float, float]]]]:
        raw: list[tuple[float, float, float]] = []
        for segment in view.segments:
            ds = abs(segment.b[0] - segment.a[0])
            dt = abs(segment.b[1] - segment.a[1])
            if ds * scale_to_mm < max(20.0, 5.0 * dt * scale_to_mm, 0.02 * view.length * scale_to_mm):
                continue
            raw.append((0.5 * (segment.a[1] + segment.b[1]), min(segment.a[0], segment.b[0]), max(segment.a[0], segment.b[0])))
        raw.sort(key=lambda item: item[0])
        tolerance = max(0.5 / scale_to_mm, 0.001 * max(view.height, 1.0))
        groups: list[list[object]] = []
        for t, start, end in raw:
            if not groups or abs(t - float(groups[-1][0])) > tolerance:
                groups.append([t, [(start, end)]])
            else:
                intervals = groups[-1][1]
                assert isinstance(intervals, list)
                groups[-1][0] = (float(groups[-1][0]) * len(intervals) + t) / (len(intervals) + 1)
                intervals.append((start, end))
        return [
            (float(t), self._merge_intervals(intervals, tolerance=max(0.5 / scale_to_mm, 1e-9)))
            for t, intervals in groups
            if isinstance(intervals, list)
        ]

    @staticmethod
    def _interval_overlap_length(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
        total = 0.0
        i = j = 0
        while i < len(a) and j < len(b):
            left = max(a[i][0], b[j][0])
            right = min(a[i][1], b[j][1])
            if right > left:
                total += right - left
            if a[i][1] < b[j][1]:
                i += 1
            else:
                j += 1
        return total

    def _dimension_fit(
        self,
        view: ViewCandidate,
        target_mm: float,
        scale_to_mm: float | None = None,
    ) -> tuple[float, float]:
        """Match a geometric separation against a real dimension in millimetres."""
        scale = view.unit_scale_to_mm if scale_to_mm is None else scale_to_mm
        bbox_error = abs(view.height * scale - target_mm) / max(target_mm, 1.0)
        best_error = bbox_error
        best_support = 0.0
        levels = self._longitudinal_levels(view, scale)
        for index, (ta, ia) in enumerate(levels):
            for tb, ib in levels[index + 1:]:
                separation_mm = abs(tb - ta) * scale
                error = abs(separation_mm - target_mm) / max(target_mm, 1.0)
                overlap = self._interval_overlap_length(ia, ib)
                support = min(1.0, overlap / max(view.length, 1.0))
                adjusted = max(0.0, error - 0.03 * support)
                if adjusted < best_error:
                    best_error = adjusted
                    best_support = support
        return best_error, best_support

    @staticmethod
    def _common_unit_scales() -> tuple[float, ...]:
        # DXF coordinates can be model millimetres or paper-like units.  Only
        # common drafting scale factors are considered; arbitrary fitting is
        # deliberately forbidden because it would distort production values.
        return (
            0.001, 0.002, 0.005, 0.01, 0.02, 0.04, 0.05,
            0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 4.0, 5.0,
            10.0, 20.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1000.0,
        )

    def _select_views(
        self,
        candidates: list[ViewCandidate],
        spec: BHSpec | None,
        declared_unit_to_mm: float | None = None,
    ) -> tuple[ViewCandidate | None, ViewCandidate | None, list[str]]:
        warnings: list[str] = []
        if not candidates:
            return None, None, ["未找到可用的 Part 几何视图"]
        if spec is None:
            front = max(candidates, key=lambda item: (item.height, len(item.segments)))
            front.unit_scale_to_mm = declared_unit_to_mm or 1.0
            return front, None, ["未识别 BH 规格，禁止生产输出；单位仅按DXF头信息暂存"]

        scales = list(self._common_unit_scales())
        if declared_unit_to_mm is not None and declared_unit_to_mm > 0:
            scales = [declared_unit_to_mm] + [
                value for value in scales if abs(value - declared_unit_to_mm) > 1e-12
            ]

        front_scores: list[tuple[float, ViewCandidate, float, float, float]] = []
        for candidate in candidates:
            for scale in scales:
                depth_fits = [self._dimension_fit(candidate, spec.depth_min, scale)]
                if abs(spec.depth_max - spec.depth_min) > 1e-9:
                    depth_fits.append(self._dimension_fit(candidate, spec.depth_max, scale))
                depth_error, depth_support = min(depth_fits, key=lambda item: item[0])
                length_error = (
                    abs(candidate.length * scale - spec.nominal_length) / spec.nominal_length
                    if spec.nominal_length and spec.nominal_length > 0
                    else 0.0
                )
                complexity = min(2.0, len(candidate.segments) / 50.0)
                scale_preference = 0.08 if abs(scale - 1.0) < 1e-12 else 0.0
                if declared_unit_to_mm is not None and declared_unit_to_mm > 0:
                    relative = abs(scale - declared_unit_to_mm) / declared_unit_to_mm
                    if relative <= self.config.header_unit_match_tolerance_ratio:
                        scale_preference += self.config.header_unit_score_bonus
                front_score = (
                    8.0 * exp(-depth_error / max(self.config.spec_dimension_tolerance_ratio, 1e-6))
                    + 3.0 * exp(-length_error / 0.10)
                    + complexity
                    + depth_support
                    + scale_preference
                )
                front_scores.append((front_score, candidate, depth_error, length_error, scale))

        front_scores.sort(key=lambda item: item[0], reverse=True)
        front_score, front, depth_error, length_error, scale = front_scores[0]
        front.unit_scale_to_mm = scale
        front.score = front_score
        front.reasons = [
            f"depth_error={depth_error:.4f}",
            f"length_error={length_error:.4f}",
            f"unit_scale_to_mm={scale:g}",
            f"segments={len(front.segments)}",
        ]

        top: ViewCandidate | None = None
        top_scores: list[tuple[float, ViewCandidate, float, float]] = []
        for candidate in candidates:
            if candidate is front:
                continue
            width_error, width_support = self._dimension_fit(candidate, spec.width, scale)
            length_error_top = (
                abs(candidate.length * scale - spec.nominal_length) / spec.nominal_length
                if spec.nominal_length and spec.nominal_length > 0
                else 0.0
            )
            top_score = (
                7.0 * exp(-width_error / max(self.config.spec_dimension_tolerance_ratio, 1e-6))
                + 2.0 * exp(-length_error_top / 0.15)
                + min(1.5, len(candidate.segments) / 40.0)
                + width_support
            )
            top_scores.append((top_score, candidate, width_error, length_error_top))
        if top_scores:
            top_scores.sort(key=lambda item: item[0], reverse=True)
            top_score, top_candidate, width_error, top_length_error = top_scores[0]
            if width_error <= max(0.20, 2.0 * self.config.spec_dimension_tolerance_ratio):
                top = top_candidate
                top.unit_scale_to_mm = scale
                top.score = top_score
                top.reasons = [
                    f"width_error={width_error:.4f}",
                    f"length_error={top_length_error:.4f}",
                    f"unit_scale_to_mm={scale:g}",
                    f"segments={len(top.segments)}",
                ]
            else:
                warnings.append(
                    f"俯视图宽度无法可靠匹配：几何高度换算后={top_candidate.height * scale:.3f} mm，"
                    f"规格宽度={spec.width:.3f} mm"
                )

        if depth_error > max(0.20, 2.0 * self.config.spec_dimension_tolerance_ratio):
            warnings.append(
                f"主视图高度匹配偏差较大：几何包围高度换算后={front.height * scale:.3f} mm，"
                f"规格深度={spec.depth_min:.3f}~{spec.depth_max:.3f} mm"
            )
        if len(front_scores) > 1 and front_scores[0][0] - front_scores[1][0] < 0.40:
            warnings.append("主视图候选得分接近，已使用规格、名义长度、单位比例和几何复杂度联合判定")

        if declared_unit_to_mm is not None and declared_unit_to_mm > 0:
            relative = abs(scale - declared_unit_to_mm) / declared_unit_to_mm
            if relative <= self.config.header_unit_match_tolerance_ratio:
                warnings.append(f"DXF $INSUNITS 与几何证据一致：1 DXF单位={scale:g} mm")
            else:
                warnings.append(
                    f"DXF $INSUNITS 声明 {declared_unit_to_mm:g} mm/单位，"
                    f"但几何最佳比例为 {scale:g} mm/单位；单位冲突将拒绝输出"
                )
        elif abs(scale - 1.0) > 1e-12:
            warnings.append(f"DXF未声明有效单位；由截面与名义长度推断 {scale:g} mm/单位")
        else:
            warnings.append("DXF未声明有效单位；由截面与名义长度验证 1 DXF单位=1 mm")
        if spec.drawing_scale_text:
            warnings.append(
                f"标题栏比例 {spec.drawing_scale_text} 仅为出图显示比例，不参与DXF坐标到毫米的换算"
            )
        return front, top, warnings

    def _depth_error(
        self, outer_depth_units: float, spec: BHSpec, scale_to_mm: float = 1.0
    ) -> float:
        outer_depth_mm = outer_depth_units * scale_to_mm
        if spec.depth_min <= outer_depth_mm <= spec.depth_max:
            return 0.0
        return min(
            abs(outer_depth_mm - spec.depth_min) / max(spec.depth_min, 1.0),
            abs(outer_depth_mm - spec.depth_max) / max(spec.depth_max, 1.0),
        )

    def _cross_section_pairs(self, view: ViewCandidate, s_value: float, tf: float) -> list[_Pair]:
        intersections: list[tuple[float, int]] = []
        for index, segment in enumerate(view.segments):
            ds = segment.b[0] - segment.a[0]
            if abs(ds) < 1e-12:
                continue
            if not (min(segment.a[0], segment.b[0]) - 1e-9 <= s_value <= max(segment.a[0], segment.b[0]) + 1e-9):
                continue
            u = (s_value - segment.a[0]) / ds
            if -1e-9 <= u <= 1.0 + 1e-9:
                t = segment.a[1] + u * (segment.b[1] - segment.a[1])
                intersections.append((t, index))
        intersections.sort(key=lambda item: item[0])

        unique: list[list[object]] = []
        dedup_tolerance = max(0.15, 0.01 * tf)
        for t, index in intersections:
            if unique and abs(t - float(unique[-1][0])) <= dedup_tolerance:
                ids = unique[-1][1]
                assert isinstance(ids, list)
                ids.append(index)
            else:
                unique.append([t, [index]])

        pair_tolerance = max(0.75, self.config.flange_pair_projection_tolerance_ratio * tf)
        pairs: list[_Pair] = []
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                low = float(unique[i][0])
                high = float(unique[j][0])
                if abs((high - low) - tf) > pair_tolerance:
                    continue
                ids_i = unique[i][1]
                ids_j = unique[j][1]
                assert isinstance(ids_i, list) and isinstance(ids_j, list)
                pairs.append(_Pair(0.5 * (low + high), low, high, tuple(ids_i + ids_j)))
        return pairs

    @staticmethod
    def _track_predictor(points: list[tuple[float, float]]):
        """Return a piecewise-linear predictor with local end extrapolation."""
        if not points:
            raise ValueError("翼板轨迹缺少预测点")
        import numpy as np

        ordered = sorted(points)
        xs = np.asarray([point[0] for point in ordered], dtype=float)
        ys = np.asarray([point[1] for point in ordered], dtype=float)
        if len(ordered) == 1:
            value = float(ys[0])
            return lambda _: value

        edge_count = min(30, len(ordered))
        left_a, left_b = np.polyfit(xs[:edge_count], ys[:edge_count], 1)
        right_a, right_b = np.polyfit(xs[-edge_count:], ys[-edge_count:], 1)

        def predict(x_value: float) -> float:
            if x_value < xs[0]:
                return float(left_a * x_value + left_b)
            if x_value > xs[-1]:
                return float(right_a * x_value + right_b)
            return float(np.interp(x_value, xs, ys))

        return predict

    def _best_depth_pair(
        self,
        pairs: list[_Pair],
        spec: BHSpec,
        scale_to_mm: float = 1.0,
    ) -> tuple[_Pair, _Pair] | None:
        """Return a credible lower/upper flange pair for a cross-section.

        A pair is accepted only when the two flange candidates are disjoint and
        their outside depth agrees with the BH specification.  This prevents a
        single flange plus a nearby detail line from being interpreted as both
        upper and lower flanges.
        """
        best: tuple[float, _Pair, _Pair] | None = None
        for lower in pairs:
            for upper in pairs:
                if upper.low <= lower.high:
                    continue
                outer_depth = upper.high - lower.low
                depth_error = self._depth_error(outer_depth, spec, scale_to_mm)
                if depth_error > self.config.flange_anchor_depth_error_ratio:
                    continue
                score = depth_error - 1e-8 * outer_depth
                if best is None or score < best[0]:
                    best = (score, lower, upper)
        return None if best is None else (best[1], best[2])

    def _trace_flange_profiles(
        self,
        front: ViewCandidate,
        spec: BHSpec,
    ) -> tuple[_FlangeTrace, _FlangeTrace, list[str]]:
        """Trace upper and lower flanges independently along global X.

        v0.3 formed a cross-section only when both flanges were present.  That
        contaminated unequal flange lengths: a longer lower flange could be
        copied into the upper trace.  The corrected implementation first learns
        two vertical tracks from reliable two-flange anchor sections, then
        assigns candidates to the lower and upper tracks independently.
        """
        scale_to_mm = front.unit_scale_to_mm
        length = front.length
        length_mm = length * scale_to_mm
        sample_count = max(20, self.config.trace_target_samples)
        step_mm = min(
            self.config.trace_max_step_mm,
            max(self.config.trace_min_step_mm, length_mm / sample_count),
        )
        sample_count = max(20, int(length_mm / step_mm))
        step = length / sample_count
        flange_thickness_units = spec.flange_thickness / scale_to_mm

        raw_sections: list[tuple[float, list[_Pair]]] = []
        lower_anchors: list[tuple[float, float]] = []
        upper_anchors: list[tuple[float, float]] = []
        for index in range(sample_count):
            s_value = front.s_min + (index + 0.5) * step
            pairs = self._cross_section_pairs(front, s_value, flange_thickness_units)
            raw_sections.append((s_value, pairs))
            anchor = self._best_depth_pair(pairs, spec, scale_to_mm)
            if anchor is not None:
                lower, upper = anchor
                lower_anchors.append((s_value, lower.center))
                upper_anchors.append((s_value, upper.center))

        minimum_anchors = min(
            self.config.flange_track_fit_min_anchors,
            max(2, sample_count // 20),
        )
        if len(lower_anchors) < minimum_anchors or len(upper_anchors) < minimum_anchors:
            raise ValueError(
                "无法建立上下翼独立轨迹："
                f"可靠截面={min(len(lower_anchors), len(upper_anchors))}，"
                f"最低要求={minimum_anchors}"
            )

        lower_predict = self._track_predictor(lower_anchors)
        upper_predict = self._track_predictor(upper_anchors)
        assignment_tolerance = max(
            self.config.flange_track_assignment_thickness_factor * flange_thickness_units,
            self.config.flange_track_assignment_depth_ratio * spec.depth_min / scale_to_mm,
        )

        sections: list[_CrossSection] = []
        for s_value, pairs in raw_sections:
            lower_pred = lower_predict(s_value)
            upper_pred = upper_predict(s_value)

            lower: _Pair | None = None
            upper: _Pair | None = None
            if pairs:
                lower_candidate = min(pairs, key=lambda pair: abs(pair.center - lower_pred))
                upper_candidate = min(pairs, key=lambda pair: abs(pair.center - upper_pred))
                lower_distance = abs(lower_candidate.center - lower_pred)
                upper_distance = abs(upper_candidate.center - upper_pred)
                if lower_distance <= assignment_tolerance:
                    lower = lower_candidate
                if upper_distance <= assignment_tolerance:
                    upper = upper_candidate
                if lower is upper and lower is not None:
                    # One physical pair can belong to only one flange track.
                    if lower_distance <= upper_distance:
                        upper = None
                    else:
                        lower = None
            sections.append(_CrossSection(s_value, lower, upper))

        lower = self._build_flange_trace(front, sections, "lower", step, flange_thickness_units)
        upper = self._build_flange_trace(front, sections, "upper", step, flange_thickness_units)
        warnings: list[str] = []
        if lower.piece_count == 0 or upper.piece_count == 0:
            raise ValueError(
                f"无法稳定形成两翼一腹：下翼候选={lower.piece_count}，上翼候选={upper.piece_count}"
            )
        warnings.append(
            "上翼、下翼分别作为独立板件识别；板数只由实体连续区间决定"
        )
        if lower.sample_step > 2.0 or upper.sample_step > 2.0:
            warnings.append(
                f"翼板采用 {step * scale_to_mm:.3f} mm 截面步长追踪；最终端点由对应翼板原始矢量线段精确回收"
            )
        return lower, upper, warnings

    def _recover_piece_edge(
        self,
        front: ViewCandidate,
        pair: _Pair,
        approximate_x: float,
        *,
        is_left: bool,
        step: float,
        tf: float,
        segment_usage: dict[int, int] | None = None,
    ) -> tuple[float, str]:
        """Recover a physical flange-piece edge from its full-thickness end line.

        Sampling establishes where flange material exists.  The exact end is
        then recovered from a short source segment that connects the outer and
        inner flange boundaries.  When the source end line is absent or broken,
        the fallback extends one full sample outward, which is conservative for
        setback calculation (it can only make the ordered plate slightly longer).
        """
        scale = front.unit_scale_to_mm
        edge_guard = max(2.0 * step, 2.0 / scale)

        y_tolerance = max(1.0 / scale, 0.30 * tf)
        search_window = max(5.0 * step, 5.0 * tf)
        max_closure_span = max(8.0 * tf, 0.06 * front.length)
        attachment_tolerance = max(0.35 / scale, self.config.endpoint_tolerance_mm / scale)
        candidates: list[tuple[float, float, float, float]] = []
        for segment in front.segments:
            x0, x1 = sorted((segment.a[0], segment.b[0]))
            y0, y1 = sorted((segment.a[1], segment.b[1]))
            if x1 < approximate_x - search_window or x0 > approximate_x + search_window:
                continue
            if x1 - x0 > max_closure_span:
                continue
            # A physical flange end line only needs to cover the complete
            # flange-thickness band.  Tekla contours often continue the same
            # line past the inner flange face into a web/notch transition, so
            # requiring the segment endpoints to equal pair.low/pair.high is
            # too strict and creates a one-sample fallback bias.
            if y0 > pair.low + y_tolerance or y1 < pair.high - y_tolerance:
                continue
            center_x = 0.5 * (x0 + x1)
            alignment_tolerance = max(
                self.config.flange_end_alignment_step_factor * step,
                self.config.flange_end_alignment_thickness_factor * tf,
            )
            if abs(center_x - approximate_x) > alignment_tolerance:
                # A full-thickness internal construction line is not an end
                # edge merely because it spans the flange band.  The physical
                # end must also align with the sampled material boundary.
                continue
            if not self._closure_attaches_to_pair_boundaries(
                front, pair, segment, attachment_tolerance
            ):
                # A short full-thickness line near a sampled boundary may be
                # an internal construction line.  It becomes a physical end
                # only when a traced flange surface actually terminates on
                # that line; otherwise using it can overstate the setback.
                continue
            if self._flange_material_continues_outside_closure(
                front, pair, segment, is_left=is_left, step=step, tf=tf
            ):
                # DXF can fragment continuous flange surfaces exactly at an
                # internal construction line.  Endpoint attachment alone is
                # then insufficient: complete same-thickness material just
                # outside proves this is not a physical plate end.
                continue
            excess_span = max(0.0, pair.low - y0) + max(0.0, y1 - pair.high)
            candidates.append((abs(center_x - approximate_x), excess_span, x0, x1))

        if candidates:
            _, _, x0, x1 = min(candidates, key=lambda item: (item[0], item[1]))
            return (x0 if is_left else x1), "由覆盖完整翼厚的实体端边精确回收"

        # A bevelled, rounded or sloped cap may have no line spanning the full
        # flange thickness.  The two source contour segments forming the last
        # valid plate section still carry the physical tip in their endpoint
        # bounding box.  Keep this local to the sampled end: a source line can
        # continue through a mid-member gap and be shared by the next plate,
        # so a remote endpoint is not part of this physical piece.
        usage = segment_usage or {}
        contour_segment_ids = [
            segment_id
            for segment_id in pair.segment_ids
            if usage.get(segment_id, 0) >= 2
            or abs(
                front.segments[segment_id].b[1]
                - front.segments[segment_id].a[1]
            ) <= 2.5 * tf
        ]
        contour_points = [
            point
            for segment_id in contour_segment_ids
            for point in (
                front.segments[segment_id].a,
                front.segments[segment_id].b,
            )
        ]
        if contour_points:
            contour_edge = (
                min(point[0] for point in contour_points)
                if is_left
                else max(point[0] for point in contour_points)
            )
            if abs(contour_edge - approximate_x) <= max_closure_span:
                return contour_edge, "由该实体板端部原始轮廓包围盒边界读取"

        # A near-end material sample is not proof that the plate is flush.
        # Prefer an exact closure line first: a real 1--2 mm setback still
        # matters after the required floor conversion.  The guard remains a
        # conservative fallback for incomplete drawings without that line.
        if is_left and approximate_x <= front.s_min + edge_guard:
            return front.s_min, "与主视图左端齐平"
        if not is_left and approximate_x >= front.s_max - edge_guard:
            return front.s_max, "与主视图右端齐平"

        fallback = approximate_x - step if is_left else approximate_x + step
        fallback = max(front.s_min, min(front.s_max, fallback))
        return fallback, "未找到完整端边，按一个采样步长向外保守扩展"

    @staticmethod
    def _segment_is_covered_by_longer_collinear_segment(
        front: ViewCandidate,
        segment_id: int,
        candidate_ids: set[int],
        tolerance: float,
    ) -> bool:
        """Return whether another selected source edge already covers this one."""
        segment = front.segments[segment_id]
        for other_id in candidate_ids:
            if other_id == segment_id:
                continue
            other = front.segments[other_id]
            odx = other.b[0] - other.a[0]
            ody = other.b[1] - other.a[1]
            other_length = hypot(odx, ody)
            segment_length = hypot(
                segment.b[0] - segment.a[0], segment.b[1] - segment.a[1]
            )
            if other_length <= segment_length + tolerance or other_length <= 1e-12:
                continue

            def covered(point: tuple[float, float]) -> bool:
                px = point[0] - other.a[0]
                py = point[1] - other.a[1]
                projection = (px * odx + py * ody) / (other_length * other_length)
                if projection < -tolerance / other_length or projection > 1.0 + tolerance / other_length:
                    return False
                distance = abs(px * ody - py * odx) / other_length
                return distance <= tolerance

            if covered(segment.a) and covered(segment.b):
                return True
        return False

    def _flange_outline_segment_ids(
        self,
        front: ViewCandidate,
        usage: dict[int, int],
        *,
        left: float,
        right: float,
        step: float,
        tf: float,
    ) -> set[int]:
        """Keep sustained plate surfaces and true end caps, not incidental hits.

        A source line belongs to the flange overlay only when it repeatedly
        supports the traced full-thickness band, or when it is a transverse cap
        attached to the recovered physical end.  This excludes web diagonals
        and internal detail lines that happen to meet a flange surface once.
        """
        selected: set[int] = set()
        edge_tolerance = max(2.5 * step, 1.25 * tf)
        for segment_id, count in usage.items():
            segment = front.segments[segment_id]
            dx = abs(segment.b[0] - segment.a[0])
            dt = abs(segment.b[1] - segment.a[1])
            coverage = count * step / max(dx, step)
            sustained_surface = count >= 2 and coverage >= 0.45
            endpoint_distance = min(
                abs(segment.a[0] - left),
                abs(segment.b[0] - left),
                abs(segment.a[0] - right),
                abs(segment.b[0] - right),
            )
            physical_end_cap = (
                endpoint_distance <= edge_tolerance
                and dt >= 0.50 * tf
                and dx <= max(3.0 * tf, 5.0 * step)
            )
            if sustained_surface or physical_end_cap:
                selected.add(segment_id)

        # A perfectly transverse cap has ds=0 and therefore never appears in
        # cross-section usage.  Recover it from the full source contour only
        # when both of its endpoints attach to already sustained flange faces;
        # this admits real plate caps but rejects long web/detail lines that
        # merely touch one flange surface.
        sustained_ids = set(selected)
        attachment_tolerance = max(0.20, 0.03 * tf)

        def point_segment_distance(
            point: tuple[float, float], candidate: LocalSegment
        ) -> float:
            dx = candidate.b[0] - candidate.a[0]
            dy = candidate.b[1] - candidate.a[1]
            length_squared = dx * dx + dy * dy
            if length_squared <= 1e-24:
                return hypot(point[0] - candidate.a[0], point[1] - candidate.a[1])
            u = (
                (point[0] - candidate.a[0]) * dx
                + (point[1] - candidate.a[1]) * dy
            ) / length_squared
            u = max(0.0, min(1.0, u))
            return hypot(
                point[0] - (candidate.a[0] + u * dx),
                point[1] - (candidate.a[1] + u * dy),
            )

        def attached(point: tuple[float, float]) -> bool:
            return any(
                point_segment_distance(point, front.segments[segment_id])
                <= attachment_tolerance
                for segment_id in sustained_ids
            )

        for segment_id, segment in enumerate(front.segments):
            if segment_id in selected:
                continue
            dx = abs(segment.b[0] - segment.a[0])
            dt = abs(segment.b[1] - segment.a[1])
            endpoint_distance = min(
                abs(segment.a[0] - left),
                abs(segment.b[0] - left),
                abs(segment.a[0] - right),
                abs(segment.b[0] - right),
            )
            if (
                endpoint_distance <= edge_tolerance
                and dt >= 0.50 * tf
                and dx <= max(3.0 * tf, 5.0 * step)
                and attached(segment.a)
                and attached(segment.b)
            ):
                selected.add(segment_id)

        collinear_tolerance = max(0.15, 0.01 * tf)
        return {
            segment_id
            for segment_id in selected
            if not self._segment_is_covered_by_longer_collinear_segment(
                front, segment_id, selected, collinear_tolerance
            )
        }

    @staticmethod
    def _closure_attaches_to_pair_boundaries(
        front: ViewCandidate,
        pair: _Pair,
        closure: LocalSegment,
        tolerance: float,
    ) -> bool:
        """Require a traced flange surface to terminate on a closure line.

        Cross-section sampling supplies the two source segments that form the
        low/high flange surfaces.  A genuine end edge must touch at least one
        of their endpoints.  Requiring both is too strict for valid Tekla
        contours, where a closure continues into a web/notch transition and
        one apparent surface can remain as a longer construction projection.
        A nearby internal vertical construction line crosses only continuing
        surfaces and must not be allowed to increase a setback.
        """
        if not pair.segment_ids:
            return False

        def endpoint_touches_closure(point: tuple[float, float]) -> bool:
            ax, ay = closure.a
            bx, by = closure.b
            dx = bx - ax
            dy = by - ay
            length_squared = dx * dx + dy * dy
            if length_squared <= 1e-18:
                return hypot(point[0] - ax, point[1] - ay) <= tolerance
            projection = ((point[0] - ax) * dx + (point[1] - ay) * dy) / length_squared
            projection = max(0.0, min(1.0, projection))
            nearest = (ax + projection * dx, ay + projection * dy)
            return hypot(point[0] - nearest[0], point[1] - nearest[1]) <= tolerance

        return any(
            endpoint_touches_closure(front.segments[segment_id].a)
            or endpoint_touches_closure(front.segments[segment_id].b)
            for segment_id in pair.segment_ids
        )

    def _flange_material_continues_outside_closure(
        self,
        front: ViewCandidate,
        pair: _Pair,
        closure: LocalSegment,
        *,
        is_left: bool,
        step: float,
        tf: float,
    ) -> bool:
        """Tell whether full material exists immediately outside a candidate edge.

        A true plate end has no matching flange pair immediately beyond its
        outward side.  This check rejects an internal line even when DXF has
        split both continuous flange boundaries at the same X coordinate.
        """
        edge = min(closure.a[0], closure.b[0]) if is_left else max(closure.a[0], closure.b[0])
        available = edge - front.s_min if is_left else front.s_max - edge
        if available <= 1e-9:
            return False
        probe_distance = min(0.5 * step, 0.5 * available)
        probe_s = edge - probe_distance if is_left else edge + probe_distance
        center_tolerance = max(
            1.0 / front.unit_scale_to_mm,
            0.50 * tf,
        )
        for outside_pair in self._cross_section_pairs(front, probe_s, tf):
            if abs(outside_pair.center - pair.center) <= center_tolerance:
                return True
        return False

    def _build_flange_trace(
        self,
        front: ViewCandidate,
        sections: list[_CrossSection],
        side: str,
        step: float,
        tf: float,
    ) -> _FlangeTrace:
        attr = "lower" if side == "lower" else "upper"
        valid = [getattr(section, attr) is not None for section in sections]

        # Tiny gaps caused by fragmented DXF lines are bridged.  The threshold
        # is deliberately much smaller than a real mid-member plate break.
        max_gap_length = max(
            self.config.flange_gap_bridge_thickness_factor * tf,
            self.config.flange_gap_bridge_ratio * front.length,
        )
        max_gap_samples = max(1, int(max_gap_length / step))
        index = 0
        while index < len(valid):
            if valid[index]:
                index += 1
                continue
            end = index
            while end < len(valid) and not valid[end]:
                end += 1
            if index > 0 and end < len(valid) and end - index <= max_gap_samples:
                for gap_index in range(index, end):
                    valid[gap_index] = True
            index = end

        minimum_piece_length = max(
            self.config.flange_min_piece_thickness_factor * tf,
            self.config.flange_min_piece_ratio * front.length,
        )
        run_indices: list[tuple[int, int]] = []
        index = 0
        while index < len(valid):
            if not valid[index]:
                index += 1
                continue
            end = index
            while end + 1 < len(valid) and valid[end + 1]:
                end += 1
            if (end - index + 1) * step >= minimum_piece_length:
                run_indices.append((index, end))
            index = end + 1

        pieces: list[_FlangePiece] = []
        all_selected_ids: set[int] = set()
        all_inner_values: list[float] = []
        all_outer_values: list[float] = []

        for piece_index, (start, end) in enumerate(run_indices, start=1):
            piece_ids: set[int] = set()
            piece_usage: dict[int, int] = defaultdict(int)
            piece_inner: list[float] = []
            piece_outer: list[float] = []
            profile_points: list[tuple[float, float]] = []
            first_pair: _Pair | None = None
            last_pair: _Pair | None = None
            for section in sections[start : end + 1]:
                pair = getattr(section, attr)
                if pair is None:
                    continue
                first_pair = first_pair or pair
                last_pair = pair
                piece_ids.update(pair.segment_ids)
                for segment_id in set(pair.segment_ids):
                    piece_usage[segment_id] += 1
                profile_points.append((section.s, pair.center))
                if side == "lower":
                    piece_outer.append(pair.low)
                    piece_inner.append(pair.high)
                else:
                    piece_inner.append(pair.low)
                    piece_outer.append(pair.high)

            if first_pair is None or last_pair is None:
                continue
            occupancy_left = max(front.s_min, sections[start].s - 0.5 * step)
            occupancy_right = min(front.s_max, sections[end].s + 0.5 * step)

            left, left_evidence = self._recover_piece_edge(
                front,
                first_pair,
                occupancy_left,
                is_left=True,
                step=step,
                tf=tf,
                segment_usage=piece_usage,
            )
            right, right_evidence = self._recover_piece_edge(
                front,
                last_pair,
                occupancy_right,
                is_left=False,
                step=step,
                tf=tf,
                segment_usage=piece_usage,
            )
            if right <= left:
                # This should never be silently emitted.  Keep the occupancy
                # interval for diagnostics and let the confidence gate reject it.
                left, right = occupancy_left, occupancy_right
                edge_evidence = "端边回收冲突，退回实体占用区间"
            else:
                edge_evidence = f"左端{left_evidence}；右端{right_evidence}"

            piece_ids = self._flange_outline_segment_ids(
                front,
                piece_usage,
                left=left,
                right=right,
                step=step,
                tf=tf,
            )

            profile_points.sort(key=lambda item: item[0])
            edge_count = max(1, min(12, len(profile_points) // 10 or 1))
            center_start = median(value for _, value in profile_points[:edge_count])
            center_end = median(value for _, value in profile_points[-edge_count:])
            pieces.append(
                _FlangePiece(
                    index=piece_index,
                    left=left,
                    right=right,
                    occupancy_run=(occupancy_left, occupancy_right),
                    segment_ids=piece_ids,
                    center_start=center_start,
                    center_end=center_end,
                    center_profile=tuple(profile_points),
                    evidence=[
                        "由完整翼厚材料连续区间识别",
                        edge_evidence,
                    ],
                )
            )
            all_selected_ids.update(piece_ids)
            all_inner_values.extend(piece_inner)
            all_outer_values.extend(piece_outer)

        pieces.sort(key=lambda piece: (piece.left, piece.right))
        for index, piece in enumerate(pieces, start=1):
            piece.index = index
        left = min((piece.left for piece in pieces), default=0.0)
        right = max((piece.right for piece in pieces), default=0.0)
        center_start = pieces[0].center_start if pieces else None
        center_end = pieces[-1].center_end if pieces else None
        runs = [piece.occupancy_run for piece in pieces]
        evidence = [f"翼板实体连续区间 {len(pieces)} 个"]
        if len(pieces) > 1:
            evidence.append("翼板材料在构件中部确实缺失，按多块实体板处理")
        return _FlangeTrace(
            side=side,
            left=left,
            right=right,
            piece_count=len(pieces),
            runs=runs,
            pieces=pieces,
            seam_positions=[],
            selected_segment_ids=all_selected_ids,
            inner_values=all_inner_values,
            outer_values=all_outer_values,
            sample_step=step,
            center_start=center_start,
            center_end=center_end,
            evidence=evidence,
        )

    @staticmethod
    def _clip_segment_to_t(
        segment: LocalSegment,
        low: float,
        high: float,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        a, b = segment.a, segment.b
        dt = b[1] - a[1]
        if abs(dt) < 1e-12:
            return (a, b) if low - 1e-9 <= a[1] <= high + 1e-9 else None
        u0, u1 = 0.0, 1.0
        for bound, lower_bound in ((low, True), (high, False)):
            value = (bound - a[1]) / dt
            if lower_bound:
                if dt > 0:
                    u0 = max(u0, value)
                else:
                    u1 = min(u1, value)
            else:
                if dt > 0:
                    u1 = min(u1, value)
                else:
                    u0 = max(u0, value)
        if u0 > u1 + 1e-12:
            return None
        return (
            (a[0] + (b[0] - a[0]) * u0, a[1] + dt * u0),
            (a[0] + (b[0] - a[0]) * u1, a[1] + dt * u1),
        )

    @staticmethod
    def _piece_center_at(piece: _FlangePiece, x_value: float) -> float | None:
        points = piece.center_profile
        if not points:
            return None
        if len(points) == 1:
            return points[0][1]
        if x_value <= points[0][0]:
            left, right = points[0], points[1]
        elif x_value >= points[-1][0]:
            left, right = points[-2], points[-1]
        else:
            low = 0
            high = len(points) - 1
            while low + 1 < high:
                middle = (low + high) // 2
                if points[middle][0] < x_value:
                    low = middle
                else:
                    high = middle
            left, right = points[low], points[high]
        if right[0] <= left[0] + 1e-12:
            return left[1]
        fraction = (x_value - left[0]) / (right[0] - left[0])
        return left[1] + fraction * (right[1] - left[1])

    @classmethod
    def _trace_inner_at(
        cls,
        front: ViewCandidate,
        trace: _FlangeTrace,
        x_value: float,
        tf: float,
    ) -> float | None:
        tolerance = max(1e-7, 1e-8 * max(1.0, abs(x_value)))
        candidates = [
            piece
            for piece in trace.pieces
            if piece.left - tolerance <= x_value <= piece.right + tolerance
        ]
        if not candidates:
            return None
        piece = min(
            candidates,
            key=lambda item: abs(x_value - 0.5 * (item.left + item.right)),
        )
        center = cls._piece_center_at(piece, x_value)
        if center is None:
            return None
        expected = center + 0.5 * tf if trace.side == "lower" else center - 0.5 * tf

        # Read the physical inner surface from the cleaned source contour.
        # The sampled centre profile is only used to select the correct one of
        # the two flange faces: a transverse web line can perturb one sampling
        # station, but it cannot move the actual longitudinal source edge.
        candidates_y: list[float] = []
        surface_tolerance = max(0.35 * tf, 2.0 * trace.sample_step)
        for segment_id in piece.segment_ids:
            segment = front.segments[segment_id]
            dx = segment.b[0] - segment.a[0]
            if abs(dx) <= 1e-12:
                continue
            u = (x_value - segment.a[0]) / dx
            if u < -tolerance or u > 1.0 + tolerance:
                continue
            y_value = segment.a[1] + u * (segment.b[1] - segment.a[1])
            if abs(y_value - expected) <= surface_tolerance:
                candidates_y.append(y_value)
        if not candidates_y:
            return expected
        return max(candidates_y) if trace.side == "lower" else min(candidates_y)

    def _clip_web_boundary_to_flange_inner_surfaces(
        self,
        front: ViewCandidate,
        segment: LocalSegment,
        lower_trace: _FlangeTrace,
        upper_trace: _FlangeTrace,
        tf: float,
        fallback_low: float,
        fallback_high: float,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """Clip one transverse source edge to the locally tracked web plate.

        The flange centre profiles are piecewise linear in X.  Split the
        source segment at every profile knot, solve its crossings with both
        local inner surfaces in each linear interval, and retain intervals
        lying between those surfaces.  A median band remains only a guarded
        fallback when a flange profile is absent at the candidate X.
        """
        ax, ay = segment.a
        bx, by = segment.b
        dx = bx - ax
        dy = by - ay
        if abs(dx) <= 1e-12:
            lower = self._trace_inner_at(front, lower_trace, ax, tf)
            upper = self._trace_inner_at(front, upper_trace, ax, tf)
            return self._clip_segment_to_t(
                segment,
                fallback_low if lower is None else lower,
                fallback_high if upper is None else upper,
            )

        x_min, x_max = sorted((ax, bx))
        knots = {0.0, 1.0}
        for trace in (lower_trace, upper_trace):
            for piece in trace.pieces:
                source_knots = [
                    point[0]
                    for segment_id in piece.segment_ids
                    for point in (front.segments[segment_id].a, front.segments[segment_id].b)
                ]
                for x_value in (piece.left, *source_knots, piece.right):
                    if x_min < x_value < x_max:
                        knots.add((x_value - ax) / dx)

        def gaps(u_value: float) -> tuple[float, float]:
            x_value = ax + dx * u_value
            y_value = ay + dy * u_value
            lower = self._trace_inner_at(front, lower_trace, x_value, tf)
            upper = self._trace_inner_at(front, upper_trace, x_value, tf)
            if lower is None:
                lower = fallback_low
            if upper is None:
                upper = fallback_high
            return y_value - lower, upper - y_value

        ordered = sorted(knots)
        roots: set[float] = set(ordered)
        for left_u, right_u in zip(ordered, ordered[1:]):
            left_gaps = gaps(left_u)
            right_gaps = gaps(right_u)
            for left_gap, right_gap in zip(left_gaps, right_gaps, strict=True):
                if left_gap * right_gap < 0.0:
                    root = left_u + (right_u - left_u) * (
                        -left_gap / (right_gap - left_gap)
                    )
                    roots.add(max(left_u, min(right_u, root)))

        refined = sorted(roots)
        valid_ranges: list[tuple[float, float]] = []
        tolerance = 1e-7
        for left_u, right_u in zip(refined, refined[1:]):
            midpoint = 0.5 * (left_u + right_u)
            lower_gap, upper_gap = gaps(midpoint)
            if lower_gap >= -tolerance and upper_gap >= -tolerance:
                valid_ranges.append((left_u, right_u))
        if not valid_ranges:
            return None
        first_u = min(item[0] for item in valid_ranges)
        last_u = max(item[1] for item in valid_ranges)
        return (
            (ax + dx * first_u, ay + dy * first_u),
            (ax + dx * last_u, ay + dy * last_u),
        )

    def _web_extents(
        self,
        view: ViewCandidate,
        lower_trace: _FlangeTrace,
        upper_trace: _FlangeTrace,
        spec: BHSpec,
    ) -> tuple[float, float, str, float]:
        if not lower_trace.inner_values or not upper_trace.inner_values:
            raise ValueError("缺少翼板内表面，无法建立腹板核心区")
        scale_to_mm = view.unit_scale_to_mm
        margin_mm = max(2.0, 0.03 * max(spec.depth_min - 2.0 * spec.flange_thickness, 1.0))
        margin = margin_mm / scale_to_mm
        material_low = median(lower_trace.inner_values)
        material_high = median(upper_trace.inner_values)
        web_low = material_low + margin
        web_high = material_high - margin
        if web_low >= web_high:
            raise ValueError("腹板核心区上下边界无效")

        web_height = web_high - web_low
        end_window = max(
            self.config.web_end_window_min_mm / scale_to_mm,
            self.config.web_end_window_ratio * view.length,
            spec.width / scale_to_mm,
        )
        end_window = min(end_window, 0.35 * view.length)
        left_limit = view.s_min + end_window
        right_limit = view.s_max - end_window
        left_values: list[float] = []
        right_values: list[float] = []

        for segment in view.segments:
            clipped = self._clip_segment_to_t(segment, web_low, web_high)
            if not clipped:
                continue
            p0, p1 = clipped
            original_dt = abs(segment.b[1] - segment.a[1])
            original_ds = abs(segment.b[0] - segment.a[0])
            clipped_dt = abs(p1[1] - p0[1])
            # A web end is a physical transverse cut through a substantial
            # portion of the clear web depth.  Long flange edges—especially
            # the sloping flange lines of a tapered BH—must never be used as
            # web end points merely because they cross the web core.
            #
            # Therefore a candidate must satisfy both conditions:
            #   1. it spans enough of the web core vertically;
            #   2. it is steep (vertical or near-vertical), rather than a
            #      longitudinal flange boundary.
            boundary_like = (
                clipped_dt >= self.config.web_boundary_min_span_ratio * web_height
                and original_dt
                >= self.config.web_boundary_min_steepness_ratio * original_ds
            )
            if not boundary_like:
                continue
            # The margin-trimmed core is used only to prove ownership.  The
            # measured bbox spans the actual web material between the two
            # flange inner surfaces.  This restores a sloped web tip lost to
            # the artificial margin without importing any continuation of the
            # same source LINE through the flange bands.
            material_clip = self._clip_web_boundary_to_flange_inner_surfaces(
                view,
                segment,
                lower_trace,
                upper_trace,
                spec.flange_thickness / scale_to_mm,
                material_low,
                material_high,
            )
            if material_clip is None:
                continue
            source_left = min(material_clip[0][0], material_clip[1][0])
            source_right = max(material_clip[0][0], material_clip[1][0])
            if min(p0[0], p1[0]) <= left_limit:
                left_values.append(source_left)
            if max(p0[0], p1[0]) >= right_limit:
                right_values.append(source_right)

        evidence: list[str] = []
        confidence = 1.0
        if left_values:
            left = min(left_values)
            evidence.append("左端腹板原始边界包围盒")
        else:
            left = view.s_min
            evidence.append("左端齐平保守回退")
            confidence -= 0.10
        if right_values:
            right = max(right_values)
            evidence.append("右端腹板原始边界包围盒")
        else:
            right = view.s_max
            evidence.append("右端齐平保守回退")
            confidence -= 0.10
        if left > right:
            raise ValueError("腹板端点顺序无效")
        return left, right, "，".join(evidence), confidence

    @staticmethod
    def _dimension_values(texts: Iterable[Primitive]) -> list[float]:
        values: list[float] = []
        for item in texts:
            if "dim" not in item.layer.lower():
                continue
            text = item.text.strip()
            if not NUMBER_RE.fullmatch(text):
                continue
            values.append(float(text))
        return values

    def _dimension_corroboration(self, left: float, right: float, values: Sequence[float]) -> str:
        evidence: list[str] = []
        for side, raw in (("左", left), ("右", right)):
            if raw <= self.config.dimension_corroboration_tolerance_mm:
                continue
            nearest = min(values, key=lambda value: abs(value - raw), default=None)
            if nearest is not None and abs(nearest - raw) <= self.config.dimension_corroboration_tolerance_mm:
                evidence.append(f"{side}进与尺寸文字 {nearest:g} mm 相互佐证")
        return "；".join(evidence)

    def _safe_integer(self, value: float) -> int:
        # Strict safety rule: never increase a genuinely fractional setback.
        # First remove only binary representation noise around an exact integer
        # (for example 99.99999999999997 from two line intersections).
        bounded = max(0.0, value)
        nearest = round(bounded)
        representation_tolerance = 1e-12 * max(1.0, abs(bounded))
        if abs(bounded - nearest) <= representation_tolerance:
            bounded = float(nearest)
        return int(floor(bounded))

    def _measure(
        self,
        front: ViewCandidate,
        top: ViewCandidate | None,
        spec: BHSpec,
        lower_trace: _FlangeTrace,
        upper_trace: _FlangeTrace,
        web_geometry: tuple[float, float, str, float],
        dimension_values: Sequence[float],
    ) -> tuple[list[PlateMeasurement], float, list[str]]:
        warnings: list[str] = []
        web_left, web_right, web_evidence, web_confidence = web_geometry
        scale_to_mm = front.unit_scale_to_mm

        def values(left: float, right: float) -> tuple[float, float, int, int]:
            left_raw = max(0.0, left - front.s_min) * scale_to_mm
            right_raw = max(0.0, front.s_max - right) * scale_to_mm
            return left_raw, right_raw, self._safe_integer(left_raw), self._safe_integer(right_raw)

        # 置信度加权公式（决定输出是否低于 minimum_confidence_to_emit 而被
        # 拒绝）：0.66 基准 + 0.14×深度拟合衰减（exp 使误差越大贡献越小）
        # + 0.08×深度支撑 + 0.10×腹板证据 + 0.08×长度校验衰减（分母 0.10
        # 为相对误差的软化尺度）+ 俯视图佐证 0.05。各权重按 199 张回归图
        # 的置信度分布校准；调整输出门槛时须同时复核回归结论。
        confidence = 0.66
        depth_error, depth_support = min(
            [self._dimension_fit(front, spec.depth_min), self._dimension_fit(front, spec.depth_max)],
            key=lambda item: item[0],
        )
        confidence += 0.14 * exp(-depth_error / max(self.config.spec_dimension_tolerance_ratio, 1e-6))
        confidence += 0.08 * depth_support
        confidence += 0.10 * web_confidence
        if spec.nominal_length:
            length_error = abs(front.length * scale_to_mm - spec.nominal_length) / max(spec.nominal_length, 1.0)
            confidence += 0.08 * exp(-length_error / 0.10)
        if top is not None:
            confidence += 0.05
            top_length_difference = abs(top.length - front.length) * scale_to_mm
            if top_length_difference > max(2.0, 0.003 * front.length * scale_to_mm):
                warnings.append("主视图与俯视图纵向包围长度不同；左右进以主视图为准")
                confidence -= 0.04
        confidence = max(0.0, min(1.0, confidence))

        web = values(web_left, web_right)
        web_dimension_evidence = self._dimension_corroboration(web[0], web[1], dimension_values)
        measurements: list[PlateMeasurement] = [
            PlateMeasurement(
                "腹",
                *web,
                confidence,
                web_evidence + (("；" + web_dimension_evidence) if web_dimension_evidence else ""),
            )
        ]

        if not upper_trace.pieces or not lower_trace.pieces:
            raise ValueError("上翼或下翼没有形成可输出的实体板件")

        def center_at_fraction(
            profile: Sequence[tuple[float, float]], fraction: float
        ) -> float | None:
            """Linearly interpolate one tracked flange centre at a normalized X.

            The absolute elevations of upper and lower flanges naturally differ.
            Shape comparison therefore uses the residual after each profile's own
            straight end-to-end trend is removed (see `profile_shape_delta_mm`).
            """
            if len(profile) < 2:
                return None
            start_s, start_center = profile[0]
            end_s, end_center = profile[-1]
            if end_s <= start_s:
                return None
            target = start_s + (end_s - start_s) * fraction
            left = 0
            right = len(profile) - 1
            while left + 1 < right:
                middle = (left + right) // 2
                if profile[middle][0] < target:
                    left = middle
                else:
                    right = middle
            s0, center0 = profile[left]
            s1, center1 = profile[right]
            if s1 <= s0:
                return center0
            return center0 + (center1 - center0) * (target - s0) / (s1 - s0)

        def profile_shape_delta_mm(
            upper_piece: _FlangePiece, lower_piece: _FlangePiece
        ) -> float:
            """Return the maximum non-linear upper/lower shape difference.

            Matching endpoint positions and matching net slope alone are not a
            sufficient identity test: one flange can have a middle crown, kink
            or taper that the other one lacks.  Sample both complete tracked
            profiles at normalized X values and compare the residual after
            subtracting each profile's straight end-to-end trend.
            """
            upper_profile = upper_piece.center_profile
            lower_profile = lower_piece.center_profile
            if len(upper_profile) < 3 or len(lower_profile) < 3:
                return 0.0
            sample_count = max(3, self.config.flange_profile_shape_samples)
            upper_start = upper_profile[0][1]
            upper_end = upper_profile[-1][1]
            lower_start = lower_profile[0][1]
            lower_end = lower_profile[-1][1]
            maximum = 0.0
            for index in range(1, sample_count - 1):
                fraction = index / (sample_count - 1)
                upper_center = center_at_fraction(upper_profile, fraction)
                lower_center = center_at_fraction(lower_profile, fraction)
                if upper_center is None or lower_center is None:
                    continue
                upper_residual = upper_center - (
                    upper_start + (upper_end - upper_start) * fraction
                )
                lower_residual = lower_center - (
                    lower_start + (lower_end - lower_start) * fraction
                )
                maximum = max(maximum, abs(upper_residual - lower_residual))
            return maximum * scale_to_mm

        def matching_flange_pair(
            upper_piece: _FlangePiece,
            lower_piece: _FlangePiece,
        ) -> tuple[
            bool,
            tuple[float, float, int, int],
            tuple[float, float, int, int],
            float,
            float,
            float,
        ]:
            """Compare corresponding upper/lower physical flange pieces.

            The physical merge rule applies to every matching segment, not
            only to a conventional one-piece flange.  A recovered endpoint
            that used the conservative sample fallback remains uncertain by
            at most its sampling step, so it must not manufacture a second
            plate merely from that bounded recovery uncertainty.
            """
            upper = values(upper_piece.left, upper_piece.right)
            lower = values(lower_piece.left, lower_piece.right)
            upper_delta_mm = (
                0.0
                if upper_piece.center_start is None or upper_piece.center_end is None
                else (upper_piece.center_end - upper_piece.center_start) * scale_to_mm
            )
            lower_delta_mm = (
                0.0
                if lower_piece.center_start is None or lower_piece.center_end is None
                else (lower_piece.center_end - lower_piece.center_start) * scale_to_mm
            )
            # Different DXF readers can represent the same flat line as an
            # IEEE-754 value infinitesimally below zero.  It must not leak into
            # evidence text as a backend-only “-0.000 mm” difference.
            if abs(upper_delta_mm) < 1e-9:
                upper_delta_mm = 0.0
            if abs(lower_delta_mm) < 1e-9:
                lower_delta_mm = 0.0
            profile_delta_mm = profile_shape_delta_mm(upper_piece, lower_piece)
            shape_tolerance_mm = max(
                self.config.flange_shape_delta_tolerance_mm,
                self.config.flange_shape_delta_tolerance_depth_ratio * spec.depth_min,
            )
            endpoint_tolerance_mm = self.config.same_flange_tolerance_mm
            fallback_used = any(
                "采样步长" in evidence
                for evidence in (*upper_piece.evidence, *lower_piece.evidence)
            )
            if fallback_used:
                endpoint_tolerance_mm = max(
                    endpoint_tolerance_mm,
                    upper_trace.sample_step * scale_to_mm
                    + lower_trace.sample_step * scale_to_mm
                    + self.config.same_flange_tolerance_mm,
                )
            same = (
                abs(upper[0] - lower[0]) <= endpoint_tolerance_mm
                and abs(upper[1] - lower[1]) <= endpoint_tolerance_mm
                and abs(upper_delta_mm - lower_delta_mm) <= shape_tolerance_mm
                and profile_delta_mm <= shape_tolerance_mm
            )
            return same, upper, lower, upper_delta_mm, lower_delta_mm, profile_delta_mm

        # A conventional BH with one upper and one lower flange preserves the
        # original compact naming: identical plates are merged as “翼”.
        if len(upper_trace.pieces) == 1 and len(lower_trace.pieces) == 1:
            upper_piece = upper_trace.pieces[0]
            lower_piece = lower_trace.pieces[0]
            flange_same, upper, lower, upper_delta_mm, lower_delta_mm, profile_delta_mm = matching_flange_pair(
                upper_piece, lower_piece
            )
            if flange_same:
                merged_left = min(upper[0], lower[0])
                merged_right = min(upper[1], lower[1])
                corroboration = self._dimension_corroboration(
                    merged_left, merged_right, dimension_values
                )
                measurements.append(
                    PlateMeasurement(
                        "翼",
                        merged_left,
                        merged_right,
                        self._safe_integer(merged_left),
                        self._safe_integer(merged_right),
                        confidence,
                        "上翼与下翼实体边界和纵向形状一致；按安全方向合并输出"
                        + (("；" + corroboration) if corroboration else ""),
                    )
                )
            else:
                for role, piece, measured, delta in (
                    ("上翼", upper_piece, upper, upper_delta_mm),
                    ("下翼", lower_piece, lower, lower_delta_mm),
                ):
                    corroboration = self._dimension_corroboration(
                        measured[0], measured[1], dimension_values
                    )
                    measurements.append(
                        PlateMeasurement(
                            role,
                            *measured,
                            confidence,
                            f"{role}独立实体板；纵向高差 {delta:.3f} mm；"
                            + f"上下翼形状残差 {profile_delta_mm:.3f} mm；"
                            + "；".join(piece.evidence)
                            + (("；" + corroboration) if corroboration else ""),
                        )
                    )
            return measurements, confidence, warnings

        # The same physical merge rule also applies to corresponding pieces
        # of multi-piece flanges.  For example, two equal upper/lower segments
        # are `翼-1` and `翼-2`, not four duplicate `上翼/下翼` rows.
        if len(upper_trace.pieces) == len(lower_trace.pieces):
            paired = [
                (upper_piece, lower_piece, matching_flange_pair(upper_piece, lower_piece))
                for upper_piece, lower_piece in zip(
                    upper_trace.pieces, lower_trace.pieces, strict=True
                )
            ]
            if all(pair[2][0] for pair in paired):
                for index, (_, _, pair) in enumerate(paired, start=1):
                    _, upper, lower, _, _, _ = pair
                    merged_left = min(upper[0], lower[0])
                    merged_right = min(upper[1], lower[1])
                    corroboration = self._dimension_corroboration(
                        merged_left, merged_right, dimension_values
                    )
                    measurements.append(
                        PlateMeasurement(
                            f"翼-{index}",
                            merged_left,
                            merged_right,
                            self._safe_integer(merged_left),
                            self._safe_integer(merged_right),
                            confidence,
                            f"上翼与下翼第 {index} 块实体边界和纵向形状一致；"
                            "按安全方向合并输出"
                            + (("；" + corroboration) if corroboration else ""),
                        )
                    )
                return measurements, confidence, warnings

        # Multi-piece flanges are normal processable geometry.  Every physical
        # plate is named and measured independently from left to right.
        for side_name, pieces in (
            ("上翼", upper_trace.pieces),
            ("下翼", lower_trace.pieces),
        ):
            for piece in pieces:
                measured = values(piece.left, piece.right)
                role = side_name if len(pieces) == 1 else f"{side_name}-{piece.index}"
                corroboration = self._dimension_corroboration(
                    measured[0], measured[1], dimension_values
                )
                measurements.append(
                    PlateMeasurement(
                        role,
                        *measured,
                        confidence,
                        f"{side_name}第 {piece.index} 块实体板；按水平X从左到右编号；"
                        + "；".join(piece.evidence)
                        + (("；" + corroboration) if corroboration else ""),
                    )
                )
        return measurements, confidence, warnings

    def _four_flange_split_alignment(
        self,
        lower: _FlangeTrace,
        upper: _FlangeTrace,
        scale_to_mm: float = 1.0,
        origin_x: float = 0.0,
    ) -> tuple[bool, dict[str, object]]:
        """Confirm four flange plates from physical mid-member discontinuity.

        A normal BH has one continuous upper flange and one continuous lower
        flange.  Four flange plates exist only when *both* flange bodies are
        physically interrupted in the middle, so each flange has two separate
        continuous X-runs.  A vertical line drawn through a continuous flange
        is merely drawing/detail evidence and is deliberately ignored.
        """
        lower_gaps = [
            (lower.runs[index][1], lower.runs[index + 1][0])
            for index in range(len(lower.runs) - 1)
        ]
        upper_gaps = [
            (upper.runs[index][1], upper.runs[index + 1][0])
            for index in range(len(upper.runs) - 1)
        ]

        gap_matches: list[dict[str, float]] = []
        for lower_gap in lower_gaps:
            for upper_gap in upper_gaps:
                lower_length = max(0.0, lower_gap[1] - lower_gap[0])
                upper_length = max(0.0, upper_gap[1] - upper_gap[0])
                overlap = max(
                    0.0,
                    min(lower_gap[1], upper_gap[1])
                    - max(lower_gap[0], upper_gap[0]),
                )
                overlap_ratio = overlap / max(min(lower_length, upper_length), 1e-9)
                if overlap_ratio >= self.config.four_flange_gap_overlap_ratio:
                    gap_matches.append({
                        "lower_start_mm": (lower_gap[0] - origin_x) * scale_to_mm,
                        "lower_end_mm": (lower_gap[1] - origin_x) * scale_to_mm,
                        "upper_start_mm": (upper_gap[0] - origin_x) * scale_to_mm,
                        "upper_end_mm": (upper_gap[1] - origin_x) * scale_to_mm,
                        "overlap_ratio": overlap_ratio,
                    })

        exact_four_aligned = (
            len(lower.runs) == 2
            and len(upper.runs) == 2
            and len(lower_gaps) == 1
            and len(upper_gaps) == 1
            and bool(gap_matches)
        )
        diagnostic: dict[str, object] = {
            "semantic_rule": "physical_mid_member_discontinuity_only",
            "internal_lines_used_as_split_evidence": False,
            "lower_piece_count": len(lower.runs),
            "upper_piece_count": len(upper.runs),
            "effective_lower_piece_count": len(lower.runs),
            "effective_upper_piece_count": len(upper.runs),
            "lower_gap_count": len(lower_gaps),
            "upper_gap_count": len(upper_gaps),
            "gap_matches": gap_matches,
            "confirmed_seam_pairs_mm": [],
            "lower_seam_candidates_mm": [],
            "upper_seam_candidates_mm": [],
            "aligned": exact_four_aligned,
        }
        if len(lower_gaps) == 1:
            diagnostic["lower_gap"] = lower_gaps[0]
            diagnostic["lower_gap_mm"] = tuple(
                (value - origin_x) * scale_to_mm for value in lower_gaps[0]
            )
        if len(upper_gaps) == 1:
            diagnostic["upper_gap"] = upper_gaps[0]
            diagnostic["upper_gap_mm"] = tuple(
                (value - origin_x) * scale_to_mm for value in upper_gaps[0]
            )
        return exact_four_aligned, diagnostic

    def _unit_diagnostics(
        self,
        front: ViewCandidate,
        spec: BHSpec | None,
        drawing: DrawingData | None = None,
    ) -> dict[str, object]:
        """Verify coordinate units independently from the title-block scale."""
        scale = float(front.unit_scale_to_mm)
        header_scale = None if drawing is None else drawing.header_unit_to_mm
        result: dict[str, object] = {
            "output_unit": "mm",
            "coordinate_unit_to_mm": scale,
            "status": "unverified",
            "coordinate_conversion_applied": abs(scale - 1.0) > 1e-12,
            "header_insunits_code": None if drawing is None else drawing.insunits_code,
            "header_insunits_name": "" if drawing is None else drawing.insunits_name,
            "header_unit_to_mm": header_scale,
            "title_drawing_scale": None if spec is None else spec.drawing_scale_text,
            "title_scale_used_for_coordinate_conversion": False,
        }
        if spec is None:
            return result

        checks: list[dict[str, object]] = []
        if header_scale is not None and header_scale > 0:
            checks.append({
                "source": "dxf_header_insunits",
                "selected_mm_per_unit": scale,
                "reference_mm_per_unit": header_scale,
                "relative_error": abs(scale - header_scale) / header_scale,
            })
        if spec.nominal_length and spec.nominal_length > 0:
            geometry_mm = front.length * scale
            checks.append({
                "source": "title_length_mm",
                "geometry_mm": geometry_mm,
                "reference_mm": spec.nominal_length,
                "relative_error": abs(geometry_mm - spec.nominal_length) / spec.nominal_length,
            })

        depth_targets = [spec.depth_min]
        if abs(spec.depth_max - spec.depth_min) > 1e-9:
            depth_targets.append(spec.depth_max)
        depth_error, depth_support = min(
            (self._dimension_fit(front, target, scale) for target in depth_targets),
            key=lambda item: item[0],
        )
        checks.append({
            "source": "bh_depth_mm",
            "geometry_bbox_mm": front.height * scale,
            "reference_mm": f"{spec.depth_min:g}~{spec.depth_max:g}",
            "relative_error": depth_error,
            "longitudinal_support": depth_support,
        })
        result["checks"] = checks

        strong_geometry = [
            item for item in checks
            if item["source"] in {"title_length_mm", "bh_depth_mm"}
            and float(item["relative_error"]) <= self.config.unit_verification_tolerance_ratio
        ]
        header_check = next((item for item in checks if item["source"] == "dxf_header_insunits"), None)
        header_matches = (
            header_check is not None
            and float(header_check["relative_error"]) <= self.config.header_unit_match_tolerance_ratio
        )
        header_conflicts = (
            header_check is not None
            and float(header_check["relative_error"]) > self.config.header_unit_match_tolerance_ratio
        )
        if header_conflicts:
            result["status"] = "conflict"
            result["evidence_count"] = len(strong_geometry)
            result["reason"] = "DXF $INSUNITS conflicts with geometry-derived scale"
            return result
        if header_matches and strong_geometry:
            result["status"] = "verified_mm"
            result["evidence_count"] = 1 + len(strong_geometry)
            result["verification_mode"] = "header_plus_geometry"
        elif header_check is None and len(strong_geometry) >= 2:
            result["status"] = "verified_mm"
            result["evidence_count"] = len(strong_geometry)
            result["verification_mode"] = "two_geometry_references"
        else:
            result["reason"] = "insufficient independent unit evidence"
        return result

    @staticmethod
    def _piece_diagnostics(piece: _FlangePiece, front: ViewCandidate) -> dict[str, object]:
        scale = front.unit_scale_to_mm
        return {
            "index": piece.index,
            "left_x_dxf": piece.left,
            "right_x_dxf": piece.right,
            "left_offset_mm": max(0.0, (piece.left - front.s_min) * scale),
            "right_offset_mm": max(0.0, (front.s_max - piece.right) * scale),
            "length_mm": max(0.0, (piece.right - piece.left) * scale),
            "occupancy_run_dxf": piece.occupancy_run,
            "occupancy_run_mm": tuple(
                (value - front.s_min) * scale for value in piece.occupancy_run
            ),
            "selected_segment_ids": sorted(piece.segment_ids),
            "center_start_mm": None
            if piece.center_start is None
            else (piece.center_start - front.t_min) * scale,
            "center_end_mm": None
            if piece.center_end is None
            else (piece.center_end - front.t_min) * scale,
            "evidence": piece.evidence,
        }

    @staticmethod
    def _trace_diagnostics(trace: _FlangeTrace, front: ViewCandidate) -> dict[str, object]:
        scale = front.unit_scale_to_mm
        return {
            "piece_count": trace.piece_count,
            "left_x_dxf": trace.left,
            "right_x_dxf": trace.right,
            "left_offset_mm": max(0.0, (trace.left - front.s_min) * scale),
            "right_offset_mm": max(0.0, (front.s_max - trace.right) * scale),
            "runs_dxf": trace.runs,
            "runs_mm": [
                ((start - front.s_min) * scale, (end - front.s_min) * scale)
                for start, end in trace.runs
            ],
            "seam_positions_dxf": trace.seam_positions,
            "seam_positions_mm": [
                (value - front.s_min) * scale for value in trace.seam_positions
            ],
            "selected_segment_ids": sorted(trace.selected_segment_ids),
            "selected_segment_count": len(trace.selected_segment_ids),
            "sample_step_mm": trace.sample_step * scale,
            "center_start_mm": None if trace.center_start is None else (trace.center_start - front.t_min) * scale,
            "center_end_mm": None if trace.center_end is None else (trace.center_end - front.t_min) * scale,
            "center_delta_mm": None
            if trace.center_start is None or trace.center_end is None
            else (trace.center_end - trace.center_start) * scale,
            "pieces": [BHAnalyzer._piece_diagnostics(piece, front) for piece in trace.pieces],
            "evidence": trace.evidence,
        }

    def _diagnostics(
        self,
        front: ViewCandidate,
        top: ViewCandidate | None,
        spec: BHSpec | None,
        drawing: DrawingData | None = None,
    ) -> dict[str, object]:
        scale = front.unit_scale_to_mm
        result: dict[str, object] = {
            "measurement_rule": "horizontal_global_x",
            "output_unit": "mm",
            "units": self._unit_diagnostics(front, spec, drawing),
            "front_view": {
                "id": front.view_id,
                "axis": front.axis,
                "unit_scale_to_mm": scale,
                "left_x_dxf": front.s_min,
                "right_x_dxf": front.s_max,
                "bottom_y_dxf": front.t_min,
                "top_y_dxf": front.t_max,
                "left_x_mm": 0.0,
                "right_x_mm": front.length * scale,
                "length_mm": front.length * scale,
                "height_mm": front.height * scale,
                "score": front.score,
                "reasons": front.reasons,
            },
            "top_view": None
            if top is None
            else {
                "id": top.view_id,
                "axis": top.axis,
                "unit_scale_to_mm": scale,
                "length_mm": top.length * scale,
                "height_mm": top.height * scale,
                "score": top.score,
                "reasons": top.reasons,
            },
            "spec": None
            if spec is None
            else {
                "depth_mm": spec.depth,
                "depth_end_mm": spec.depth_end,
                "depth_min_mm": spec.depth_min,
                "depth_max_mm": spec.depth_max,
                "width_mm": spec.width,
                "web_thickness_mm": spec.web_thickness,
                "flange_thickness_mm": spec.flange_thickness,
                "nominal_length_mm": spec.nominal_length,
                "drawing_scale_text": spec.drawing_scale_text,
                "drawing_scale_denominator": spec.drawing_scale_denominator,
                "raw_text": spec.raw_text,
            },
        }
        if drawing is not None:
            result["unsupported_source_entities"] = self._unsupported_diagnostics(drawing)
        return result

    @staticmethod
    def _unsupported_diagnostics(drawing: DrawingData) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in drawing.unsupported_geometry:
            identity = (item.kind, item.layer, item.source_block, item.source_handle)
            if identity in seen:
                continue
            seen.add(identity)
            result.append({
                "kind": item.kind,
                "layer": item.layer,
                "source_block": item.source_block,
                "source_handle": item.source_handle,
                "reason": item.reason,
            })
        return result
