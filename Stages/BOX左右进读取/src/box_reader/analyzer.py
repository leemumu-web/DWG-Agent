from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations
from math import exp, floor, hypot
from statistics import median

from .model import (
    BoardRole,
    BoxSpec,
    DrawingData,
    DrawingResult,
    LocalSegment,
    PlateMeasurement,
    Primitive,
    ViewCandidate,
)

BOX_SPEC_RE = re.compile(
    r"\bBOX\s*(\d+(?:\.\d+)?)"
    r"\s*[*xX×]\s*(\d+(?:\.\d+)?)"
    r"\s*[*xX×]\s*(\d+(?:\.\d+)?)"
    r"\s*[*xX×]\s*(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
SCALE_RE = re.compile(r"^\s*1\s*[:：]\s*(\d+(?:\.\d+)?)\s*$")


@dataclass(slots=True)
class AnalyzerConfig:
    endpoint_tolerance_mm: float = 0.20
    part_layer_regex: str = r"(?i)^part$"
    partmark_layer_regex: str = r"(?i)^partmark$"
    section_layer_regex: str = r"(?i)^section$"
    minimum_view_segments: int = 4
    view_height_tolerance_mm: float = 1.5
    flange_band_tolerance_mm: float = 2.0
    spec_dimension_tolerance_ratio: float = 0.12
    unit_verification_tolerance_ratio: float = 0.02
    dimension_corroboration_tolerance_mm: float = 0.50


def _part_layers(config: AnalyzerConfig) -> re.Pattern:
    return re.compile(config.part_layer_regex)


def _partmark_layers(config: AnalyzerConfig) -> re.Pattern:
    return re.compile(config.partmark_layer_regex)


def _section_layers(config: AnalyzerConfig) -> re.Pattern:
    return re.compile(config.section_layer_regex)


class BoxAnalyzer:
    """BOX 左右进读取器的核心分析器。

    领域决策：主视图以带 PartMark 的视图为准（优先于 Section 剖面符号）；
    上下腹板投影重叠时合并输出；端部窗口按 ``max(2*tf, 40mm)`` 扩展；
    左右进构件按拆板 equivalence 语义成对合并/区分输出。已知限制：
    折线构件标红、坡口信息论不可区分。安全取整与单位验证同 BHAnalyzer
    （fail-closed）。
    """

    def __init__(self, config: AnalyzerConfig | None = None):
        self.config = config or AnalyzerConfig()
        self._part_re = _part_layers(self.config)
        self._partmark_re = _partmark_layers(self.config)
        self._section_re = _section_layers(self.config)

    # ------------------------------------------------------------------
    # 规格与零件号
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_spec(texts: Iterable[Primitive]) -> BoxSpec | None:
        matches: list[tuple[BoxSpec, str]] = []
        for item in texts:
            text = item.text.strip()
            match = BOX_SPEC_RE.search(text)
            if not match:
                continue
            depth = float(match.group(1))
            width = float(match.group(2))
            # BOX 规格 = H * W * tw * tf：第 3 个数字是腹板厚，第 4 个是翼板厚。
            # 主视图（正面）显示翼板厚（内线距边 = tf），俯视图显示腹板厚（内线距边 = tw）。
            web_thickness = float(match.group(3))
            flange_thickness = float(match.group(4))
            if (
                depth <= 0 or width <= 0
                or flange_thickness <= 0 or web_thickness <= 0
            ):
                continue
            matches.append((
                BoxSpec(
                    depth=depth,
                    width=width,
                    flange_thickness=flange_thickness,
                    web_thickness=web_thickness,
                    raw_text=match.group(0),
                ),
                item.source_block,
            ))
        if not matches:
            return None
        # Prefer the spec that appears in the drawing sheet area (non-Part block),
        # otherwise the most common value.
        by_block: dict[str, list[BoxSpec]] = {}
        for spec, block in matches:
            by_block.setdefault(block, []).append(spec)
        best_block = max(by_block, key=lambda block: len(by_block[block]))
        return by_block[best_block][0]

    @staticmethod
    def _extract_part_number(drawing: DrawingData) -> str:
        """Read the part number from the PartMark layer text."""
        candidates: list[str] = []
        for item in drawing.texts:
            if "partmark" not in item.layer.lower():
                continue
            text = item.text.strip()
            if not text:
                continue
            # Tekla part marks can carry suffixes like `\M+XXXX`; strip them.
            cleaned = re.sub(r"\\[MUm].*$", "", text).strip()
            if cleaned:
                candidates.append(cleaned)
        if candidates:
            return min(candidates, key=len)
        # Fallback: any standalone token that looks like a part number.
        for item in drawing.texts:
            text = item.text.strip()
            if re.match(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}$", text):
                return text
        return ""

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

    # ------------------------------------------------------------------
    # Step 1: 定位主视图
    # ------------------------------------------------------------------

    @staticmethod
    def _make_view_candidate(
        view_id: str,
        segments: list[LocalSegment],
        primitives: list[Primitive],
        t_min_override: float | None = None,
        t_max_override: float | None = None,
    ) -> ViewCandidate | None:
        if not segments:
            return None
        x_values = [point for segment in segments for point in (segment.a[0], segment.b[0])]
        y_values = [point for segment in segments for point in (segment.a[1], segment.b[1])]
        return ViewCandidate(
            view_id=view_id,
            segments=segments,
            s_min=min(x_values),
            s_max=max(x_values),
            t_min=min(y_values) if t_min_override is None else t_min_override,
            t_max=max(y_values) if t_max_override is None else t_max_override,
            primitives=primitives,
        )

    @staticmethod
    def _primitive_overlaps(
        primitive: Primitive,
        y_lo: float,
        y_hi: float,
    ) -> float:
        y_min = min(point[1] for point in primitive.points)
        y_max = max(point[1] for point in primitive.points)
        return max(0.0, min(y_max, y_hi) - max(y_min, y_lo))

    def _split_block_views(
        self,
        block: str,
        segments: list[LocalSegment],
        primitives: list[Primitive],
        spec: BoxSpec,
        scale: float,
    ) -> list[ViewCandidate]:
        """Split one Part block into view candidates.

        Most BOX drawings place exactly one view per anonymous block, so the
        block as a whole is the candidate.  A rare drawing (for example h-4)
        packs several views into one block; there we split by the horizontal
        surface-line pairs whose y separation equals the BOX depth/width.
        """
        block_candidate = self._make_view_candidate(block, segments, primitives)
        if block_candidate is None:
            return []
        tol = self.config.view_height_tolerance_mm / scale
        if (
            abs(block_candidate.height - spec.depth) <= max(tol, 1.5 * tol)
            or abs(block_candidate.height - spec.width) <= max(tol, 1.5 * tol)
        ):
            return [block_candidate]

        width = block_candidate.length
        hlines: list[tuple[float, float, float]] = []
        for segment in segments:
            dy = abs(segment.b[1] - segment.a[1])
            if dy > self.config.flange_band_tolerance_mm:
                continue
            length = abs(segment.b[0] - segment.a[0])
            if length > 0.3 * max(width, 1.0):
                hlines.append((0.5 * (segment.a[1] + segment.b[1]), min(segment.a[0], segment.b[0]), max(segment.a[0], segment.b[0])))
        if len(hlines) < 2:
            return [block_candidate]

        regions: list[tuple[float, float, str]] = []
        for target, role in ((spec.depth, "front"), (spec.width, "top")):
            for index, (y1, _x_lo1, _x_hi1) in enumerate(hlines):
                for y2, _x_lo2, _x_hi2 in hlines[index + 1:]:
                    if abs(abs(y2 - y1) - target) <= tol:
                        regions.append((min(y1, y2), max(y1, y2), role))
        if not regions:
            return [block_candidate]

        # 折线（多段弯折）构件：块包围盒高度被斜段拉大（h-4 系列 2365 vs
        # H=800），但构件本体是连续折线路径。检测"长斜线 + 截面线对（y 差 =
        # H/W）并存"→ 整块即主视图候选，绝不可按"y 差 = H 的线对"把构件
        # 拦腰截断（那只是某一折线段的顶底线，不是视图边界）。
        slant_segments = [
            segment for segment in segments
            if abs(segment.b[1] - segment.a[1]) > tol
            and abs(segment.b[0] - segment.a[0]) > tol
            and hypot(
                segment.b[0] - segment.a[0],
                segment.b[1] - segment.a[1],
            ) >= 0.1 * max(width, 1.0)
        ]
        if slant_segments:
            tol_local = self.config.view_height_tolerance_mm / scale
            pair_ys = sorted({
                round(0.5 * (segment.a[1] + segment.b[1]), 1)
                for segment in segments
                if abs(segment.b[1] - segment.a[1])
                <= self.config.flange_band_tolerance_mm
                and abs(segment.b[0] - segment.a[0]) > 0.3 * max(width, 1.0)
            })
            from itertools import combinations

            if any(
                abs(abs(y2 - y1) - spec.depth) <= max(tol_local, 1.5 * tol_local)
                or abs(abs(y2 - y1) - spec.width) <= max(tol_local, 1.5 * tol_local)
                for y1, y2 in combinations(pair_ys, 2)
            ):
                return [block_candidate]

        views: list[ViewCandidate] = []
        seen: set[tuple[float, float]] = set()
        for lo, hi, _role in regions:
            key = (round(lo, 2), round(hi, 2))
            if key in seen:
                continue
            seen.add(key)
            view_segments: list[LocalSegment] = []
            view_primitives: list[Primitive] = []
            for segment in segments:
                seg_lo = min(segment.a[1], segment.b[1])
                seg_hi = max(segment.a[1], segment.b[1])
                # Boundary lines (top/bottom surface exactly at lo/hi) belong
                # to the view; allow touching the region edges.
                if seg_lo <= hi and seg_hi >= lo:
                    view_segments.append(segment)
            for primitive in primitives:
                if self._primitive_overlaps(primitive, lo, hi) >= 0.0:
                    view_primitives.append(primitive)
            candidate = self._make_view_candidate(
                f"{block}#{lo:.0f}",
                view_segments,
                view_primitives,
                t_min_override=lo,
                t_max_override=hi,
            )
            if candidate is not None and len(view_segments) >= self.config.minimum_view_segments:
                views.append(candidate)
        return views or [block_candidate]

    def _view_candidates(
        self,
        drawing: DrawingData,
        spec: BoxSpec,
    ) -> list[ViewCandidate]:
        grouped: dict[str, list[LocalSegment]] = {}
        grouped_primitives: dict[str, list[Primitive]] = {}
        for primitive in drawing.primitives:
            if not self._part_re.match(primitive.layer):
                continue
            if len(primitive.points) < 2:
                continue
            block = primitive.source_block or "MODELSPACE"
            grouped_primitives.setdefault(block, []).append(primitive)
            for a, b in zip(primitive.points, primitive.points[1:], strict=False):
                if hypot(b[0] - a[0], b[1] - a[1]) <= self.config.endpoint_tolerance_mm:
                    continue
                grouped.setdefault(block, []).append(
                    LocalSegment(
                        a, b,
                        primitive.layer,
                        primitive.source_block,
                        primitive.source_handle,
                    )
                )
        scale = (
            drawing.header_unit_to_mm
            if drawing.header_unit_to_mm is not None and drawing.header_unit_to_mm > 0
            else 1.0
        )
        candidates: list[ViewCandidate] = []
        for block, segments in grouped.items():
            if len(segments) < self.config.minimum_view_segments:
                continue
            candidates.extend(
                self._split_block_views(
                    block, segments, grouped_primitives.get(block, []), spec, scale
                )
            )
        return candidates

    def _marker_point(self, drawing: DrawingData, layer_re: re.Pattern) -> tuple[float, float] | None:
        """Centroid of marker-layer geometry (PartMark or Section)."""
        xs: list[float] = []
        ys: list[float] = []
        for primitive in drawing.primitives:
            if not layer_re.match(primitive.layer):
                continue
            for x, y in primitive.points:
                xs.append(x)
                ys.append(y)
        for primitive in drawing.texts:
            if not layer_re.match(primitive.layer):
                continue
            for x, y in primitive.points:
                xs.append(x)
                ys.append(y)
        if not xs:
            return None
        return (median(xs), median(ys))

    @staticmethod
    def _marker_below_view(view: ViewCandidate, marker: tuple[float, float]) -> bool:
        mx, my = marker
        return (
            view.s_min - 0.5 * view.length <= mx <= view.s_max + 0.5 * view.length
            and my >= view.t_max - 1e-6
            and my <= view.t_max + 0.5 * view.length
        )

    def _step1_locate_main_view(
        self,
        drawing: DrawingData,
        spec: BoxSpec,
    ) -> tuple[ViewCandidate | None, ViewCandidate | None, list[str]]:
        """Step 1: locate the front view (and optional top view) with global X bounds."""
        warnings: list[str] = []
        candidates = self._view_candidates(drawing, spec)
        if not candidates:
            return None, None, ["未找到 Part 几何视图"]

        partmark = self._marker_point(drawing, self._partmark_re)
        section_marker = self._marker_point(drawing, self._section_re)
        if partmark is None:
            warnings.append("未发现 PartMark 零件标识层，改用几何高度判定主视图")
        if section_marker is not None:
            warnings.append("检测到品红剖面标识（Section 层），其对应视图不作主视图")

        scale = (
            drawing.header_unit_to_mm
            if drawing.header_unit_to_mm is not None and drawing.header_unit_to_mm > 0
            else 1.0
        )
        tol = self.config.view_height_tolerance_mm / scale

        def front_score(candidate: ViewCandidate) -> tuple[float, float, float]:
            """(height_match, partmark_anchor, anti_section)."""
            height_error = abs(candidate.height - spec.depth)
            # 折线（多段弯折）构件的整块包围盒高度被斜段拉大：内部存在
            # y 差 = H 的截面线对（某一折线段的顶底线）即视为高度匹配。
            if height_error > max(tol, 1.5 * tol):
                line_ys = sorted({
                    round(0.5 * (segment.a[1] + segment.b[1]), 1)
                    for segment in candidate.segments
                    if abs(segment.b[1] - segment.a[1])
                    <= self.config.flange_band_tolerance_mm
                    and abs(segment.b[0] - segment.a[0])
                    > 0.3 * max(candidate.length, 1.0)
                })
                from itertools import combinations

                if any(
                    abs(abs(y2 - y1) - spec.depth) <= max(tol, 1.5 * tol)
                    for y1, y2 in combinations(line_ys, 2)
                ):
                    height_error = 0.0
            mark_anchor = 0.0
            if partmark is not None and self._marker_below_view(candidate, partmark):
                mark_anchor = 1.0
            # A view that carries a section marker is a section drawing, not front.
            anti_section = 0.0
            if section_marker is not None and self._marker_below_view(candidate, section_marker):
                anti_section = -1.0
            return (height_error, mark_anchor, anti_section)

        def top_score(candidate: ViewCandidate) -> tuple[float, float]:
            height_error = abs(candidate.height - spec.width)
            mark_anchor = 0.0
            if partmark is not None and self._marker_below_view(candidate, partmark):
                mark_anchor = 1.0
            return (height_error, mark_anchor)

        ranked = sorted(
            candidates,
            key=lambda candidate: (
                front_score(candidate)[0],
                -front_score(candidate)[1],
                -front_score(candidate)[2],
            ),
        )
        # Choose front: smallest height error, preferring PartMark-anchored views.
        best = ranked[0]
        best_height_error = front_score(best)[0]
        if best_height_error > tol:
            # No geometric height match; still allow PartMark anchor to select front.
            mark_anchored = [c for c in candidates if front_score(c)[1] > 0]
            if mark_anchored:
                best = mark_anchored[0]
            else:
                return None, None, [
                    f"主视图高度无法匹配 BOX 深度 {spec.depth:g} mm；且无 PartMark 零件标识锚定"
                ]
        front = best
        front.role = "front"
        front.unit_scale_to_mm = scale

        # Select top view: height matches width, not the front view.
        top: ViewCandidate | None = None
        top_ranked = sorted(
            [c for c in candidates if c is not front],
            key=lambda candidate: (
                top_score(candidate)[0],
                -top_score(candidate)[1],
            ),
        )
        if top_ranked:
            candidate = top_ranked[0]
            if abs(candidate.height - spec.width) <= max(tol, 1.5 * tol):
                top = candidate
                top.role = "top"
                top.unit_scale_to_mm = scale
        return front, top, warnings

    # ------------------------------------------------------------------
    # Step 2/3: 板件识别与左右进读取
    # ------------------------------------------------------------------

    @staticmethod
    def _primitive_geom(primitive: Primitive) -> tuple[float, float, float, float]:
        """Return (y_min, y_max, x_lo, x_hi) of one Part primitive."""
        y_min = min(point[1] for point in primitive.points)
        y_max = max(point[1] for point in primitive.points)
        x_lo = min(point[0] for point in primitive.points)
        x_hi = max(point[0] for point in primitive.points)
        return y_min, y_max, x_lo, x_hi

    def _key_horizontal_lines(
        self,
        primitives: list[Primitive],
        view_length: float,
        *,
        min_ratio: float = 0.40,
    ) -> list[tuple[float, float, float]]:
        """Return merged long horizontal lines as (y, x_lo, x_hi).

        ``min_ratio`` is the minimum segment length as a fraction of the view
        length; cranked (multi-kink) members have every straight segment shorter
        than 40% of the member length, so the caller relaxes it there.
        """
        from collections import defaultdict

        hlines_by_y: dict[float, list[tuple[float, float]]] = defaultdict(list)
        for primitive in primitives:
            y_min, y_max, x_lo, x_hi = self._primitive_geom(primitive)
            if y_max - y_min > 0.5:
                continue
            if x_hi - x_lo < min_ratio * max(view_length, 1.0):
                continue
            hlines_by_y[round(0.5 * (y_min + y_max), 1)].append((x_lo, x_hi))
        result: list[tuple[float, float, float]] = []
        for y, intervals in hlines_by_y.items():
            ordered = sorted(intervals)
            merged = [list(ordered[0])]
            for a, b in ordered[1:]:
                if a <= merged[-1][1] + 1.0:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            for a, b in merged:
                if b - a > min_ratio * max(view_length, 1.0):
                    result.append((y, a, b))
        return result

    def _step2_identify_plates(
        self,
        front: ViewCandidate,
        top: ViewCandidate | None,
        spec: BoxSpec,
    ) -> tuple[dict[str, tuple[float, float]], dict[str, str]]:
        """Identify top/bottom flange and web X bounds.

        Flanges come from the front view.  The BOX web is split into upper web
        (WEB_LEFT) and lower web (WEB_RIGHT) exactly like the splitter: the top
        view shows the two webs on opposite sides.  When both webs have the same
        horizontal bounds they are merged into one `腹板` (×2), mirroring the
        splitter's WEB_LEFT+WEB_RIGHT equivalence grouping.
        """
        primitives = front.primitives
        y_top = front.t_max
        y_bottom = front.t_min
        tf = spec.flange_thickness
        web_span = y_top - tf - (y_bottom + tf)
        tolerance = max(self.config.flange_band_tolerance_mm, 0.01 * tf)
        # 折线（多段弯折）构件：每段直线都比 40% 全长短，放宽水平线阈值；
        # 骨架 = 整块边界（折线路径端点），而非部分线段的并集。
        cranked = self._cranked_section_height(front, spec) is not None
        hlines = self._key_horizontal_lines(
            primitives,
            front.length,
            min_ratio=0.15 if cranked else 0.40,
        )
        # The skeleton X range is the union of the long horizontal surface lines
        # (top/bottom flanges).  Geometry outside it -- connector lines that a
        # packed multi-view block draws toward a neighbouring view -- can never
        # be a plate edge of this member.
        if cranked:
            skeleton_lo = front.s_min
            skeleton_hi = front.s_max
        else:
            skeleton_lo = min(line[1] for line in hlines) if hlines else front.s_min
            skeleton_hi = max(line[2] for line in hlines) if hlines else front.s_max
        skeleton_margin = max(3.0 * tf, 80.0)

        plates: dict[str, tuple[float, float]] = {}
        evidence: dict[str, str] = {}

        def prim_overlap(primitive: Primitive, y_lo: float, y_hi: float) -> float:
            y_min, y_max, _x_lo, _x_hi = self._primitive_geom(primitive)
            return max(0.0, min(y_max, y_hi) - max(y_min, y_lo))

        def within_skeleton(primitive: Primitive) -> bool:
            y_min, y_max, x_lo, x_hi = self._primitive_geom(primitive)
            center = 0.5 * (x_lo + x_hi)
            if skeleton_lo - skeleton_margin <= center <= skeleton_hi + skeleton_margin:
                return True
            # Centre outside the flange skeleton: keep only short plate-edge
            # segments (a web end arc, a taper closure).  A long line that
            # spans most of the member height is a connector to a neighbouring
            # view in a packed multi-view block, never a plate edge.
            y_span = y_max - y_min
            member_height = y_top - y_bottom
            return y_span <= 0.6 * max(member_height, 1.0)

        def band_hits(
            y_lo: float,
            y_hi: float,
            *,
            span: float,
            coverage_floor: float,
            include_horizontal: bool = True,
        ) -> list[Primitive]:
            hits: list[Primitive] = []
            horizontal_dy = min(0.5, 0.1 * span)
            for primitive in primitives:
                if not within_skeleton(primitive):
                    continue
                y_min, y_max, _x_lo, _x_hi = self._primitive_geom(primitive)
                dy = y_max - y_min
                if dy <= horizontal_dy:
                    # The web band is bounded by the two flange inner-surface
                    # lines; those horizontal lines belong to the flanges, never
                    # to the web.  So the web excludes horizontal band members.
                    if include_horizontal and y_lo - tolerance <= 0.5 * (y_min + y_max) <= y_hi + tolerance:
                        hits.append(primitive)
                    continue
                if prim_overlap(primitive, y_lo, y_hi) >= coverage_floor:
                    hits.append(primitive)
            return hits

        def clip_x_to_band(primitive: Primitive, y_lo: float, y_hi: float) -> tuple[float, float] | None:
            """X extremes of a primitive clipped to the y-band (where the closure
            actually meets THIS plate), never the whole-line extremes.  This is
            what stops a long taper/sloped end face of a tapered BOX from
            dragging the boundary to the far end of the taper."""
            xs: list[float] = []
            points = primitive.points
            for index in range(len(points) - 1):
                ax, ay = points[index]
                bx, by = points[index + 1]
                if ay == by:
                    if y_lo <= ay <= y_hi:
                        xs.extend((ax, bx))
                    continue
                t_lo = (y_lo - ay) / (by - ay)
                t_hi = (y_hi - ay) / (by - ay)
                t1, t2 = sorted((t_lo, t_hi))
                t1 = max(0.0, t1)
                t2 = min(1.0, t2)
                if t2 >= t1:
                    xs.append(ax + t1 * (bx - ax))
                    xs.append(ax + t2 * (bx - ax))
            if not xs:
                return None
            return (min(xs), max(xs))

        def extents_with_end_window(
            material_x_lo: float,
            material_x_hi: float,
            y_lo: float,
            y_hi: float,
            *,
            window: float,
            coverage_floor: float,
            include_horizontal: bool = True,
            exclude_outside_band: bool = False,
        ) -> tuple[float, float, int, int]:
            """Material span + end-window closures extending the boundary.

            The end window reaches only as far as a local end feature (taper or
            arc) can physically extend from the material edge; closures found
            there that overlap the band move the boundary outward, but only up
            to the X where the closure actually meets THIS plate band (clipped),
            never to the far end of a shared taper.
            """
            band_tol = max(tolerance, 0.25 * (y_hi - y_lo))
            left_extra = material_x_lo
            right_extra = material_x_hi
            left_count = 0
            right_count = 0
            for primitive in primitives:
                if not within_skeleton(primitive):
                    continue
                y_min, y_max, _seg_x_lo, _seg_x_hi = self._primitive_geom(primitive)
                if exclude_outside_band and (
                    y_min < y_lo - band_tol or y_max > y_hi + band_tol
                ):
                    # A full-height outer taper ends in the flange surface bands,
                    # not in the web; it must never move the web boundary.
                    continue
                if not include_horizontal and y_max - y_min <= 0.5:
                    continue
                mid_y = 0.5 * (y_min + y_max)
                if not (y_lo - 1.0 <= mid_y <= y_hi + 1.0):
                    continue
                if prim_overlap(primitive, y_lo, y_hi) < coverage_floor:
                    continue
                clipped = clip_x_to_band(primitive, y_lo, y_hi)
                if clipped is None:
                    continue
                clip_lo, clip_hi = clipped
                if clip_lo <= material_x_lo + 2.0 and clip_hi >= material_x_lo - window and clip_lo < left_extra:
                    left_extra = clip_lo
                    left_count += 1
                if clip_hi >= material_x_hi - 2.0 and clip_lo <= material_x_hi + window and clip_hi > right_extra:
                    right_extra = clip_hi
                    right_count += 1
            return left_extra, right_extra, left_count, right_count

        # ---- 上翼：顶线为材料骨架 ----
        top_lines = [line for line in hlines if line[0] >= y_top - tf - tolerance]
        if top_lines:
            top_line = max(top_lines, key=lambda line: line[0])
            inner_lines = [
                line for line in hlines
                if abs(line[0] - (top_line[0] - tf)) <= max(tolerance, 0.25 * tf)
            ]
            material_x_lo = min(
                [top_line[1]] + [line[1] for line in inner_lines]
            )
            material_x_hi = max(
                [top_line[2]] + [line[2] for line in inner_lines]
            )
            window = max(2.0 * tf, 40.0)
            x_lo, x_hi, left_hits, right_hits = extents_with_end_window(
                material_x_lo, material_x_hi,
                y_top - tf, y_top,
                window=window,
                coverage_floor=0.40 * tf,
            )
            plates[BoardRole.TOP_FLANGE.value] = (x_lo, x_hi)
            evidence[BoardRole.TOP_FLANGE.value] = (
                f"上翼顶线/内线材料区间[{material_x_lo:.1f},{material_x_hi:.1f}]"
                f"+端部封口({left_hits}左/{right_hits}右)"
            )
        else:
            top_hits = band_hits(y_top - tf, y_top, span=tf, coverage_floor=0.70 * tf)
            if top_hits:
                x_values = [x for primitive in top_hits for x, _y in primitive.points]
                plates[BoardRole.TOP_FLANGE.value] = (min(x_values), max(x_values))
                evidence[BoardRole.TOP_FLANGE.value] = "上翼带源边包围盒（无顶线骨架）"
            else:
                plates[BoardRole.TOP_FLANGE.value] = (front.s_min, front.s_max)
                evidence[BoardRole.TOP_FLANGE.value] = "上翼带无源边，保守回退主视图边界"

        # ---- 下翼：底线为材料骨架 ----
        bottom_lines = [line for line in hlines if line[0] <= y_bottom + tf + tolerance]
        if bottom_lines:
            bottom_line = min(bottom_lines, key=lambda line: line[0])
            inner_lines = [
                line for line in hlines
                if abs(line[0] - (bottom_line[0] + tf)) <= max(tolerance, 0.25 * tf)
            ]
            material_x_lo = min(
                [bottom_line[1]] + [line[1] for line in inner_lines]
            )
            material_x_hi = max(
                [bottom_line[2]] + [line[2] for line in inner_lines]
            )
            window = max(2.0 * tf, 40.0)
            x_lo, x_hi, left_hits, right_hits = extents_with_end_window(
                material_x_lo, material_x_hi,
                y_bottom, y_bottom + tf,
                window=window,
                coverage_floor=0.40 * tf,
            )
            plates[BoardRole.BOTTOM_FLANGE.value] = (x_lo, x_hi)
            evidence[BoardRole.BOTTOM_FLANGE.value] = (
                f"下翼底线/内线材料区间[{material_x_lo:.1f},{material_x_hi:.1f}]"
                f"+端部封口({left_hits}左/{right_hits}右)"
            )
        else:
            bottom_hits = band_hits(y_bottom, y_bottom + tf, span=tf, coverage_floor=0.70 * tf)
            if bottom_hits:
                x_values = [x for primitive in bottom_hits for x, _y in primitive.points]
                plates[BoardRole.BOTTOM_FLANGE.value] = (min(x_values), max(x_values))
                evidence[BoardRole.BOTTOM_FLANGE.value] = "下翼带源边包围盒（无底线骨架）"
            else:
                plates[BoardRole.BOTTOM_FLANGE.value] = (front.s_min, front.s_max)
                evidence[BoardRole.BOTTOM_FLANGE.value] = "下翼带无源边，保守回退主视图边界"

        # ---- 腹板：主视图竖板源边（与拆板分腹板逻辑对齐）----
        # BOX 有上腹(WEB_LEFT)/下腹(WEB_RIGHT)两块腹板，主视图正面左右腹板投影重叠，
        # 合并输出"腹板"（对应拆板 WEB_LEFT+WEB_RIGHT 成对等价合并"腹"）；左右腹板各自
        # 边界在诊断 top_webs（俯视图两侧）提供，需要区分上腹/下腹时使用。
        # 腹板带上下边界是翼板内表面线（水平线），归属翼板；腹板只统计非水平竖板线，
        # 且端部斜切/坡口线必须裁剪到腹板带（clip_x_to_band），绝不能用斜线的全局端点
        # （端点常落在翼板表面带外，会把翼板角错误计入腹板）。
        web_hits = band_hits(
            y_bottom + tf, y_top - tf,
            span=web_span,
            coverage_floor=0.30 * web_span,
            include_horizontal=False,
            )
        web_x_values: list[float] = []
        web_band_tol = max(tolerance, 0.25 * tf)
        if web_hits:
            for primitive in web_hits:
                _y_min, _y_max, _x_lo, _x_hi = self._primitive_geom(primitive)
                # The member's outer taper spans the whole height and ends in the
                # flange surface bands; it is NOT the web's own end.  Only source
                # edges that stay inside the web band define the web boundary.
                if (
                    _y_min < y_bottom + tf - web_band_tol
                    or _y_max > y_top - tf + web_band_tol
                ):
                    continue
                clipped = clip_x_to_band(primitive, y_bottom + tf, y_top - tf)
                if clipped is not None:
                    web_x_values.extend(clipped)
        # 翼板内线端点（下翼内线/上翼内线）是腹板的四角，即使斜切/连接线被过滤
        # 也应成为腹板边界。
        inner_tol = max(tolerance, 0.25 * tf)
        for line in hlines:
            if (
                abs(line[0] - (y_bottom + tf)) <= inner_tol
                or abs(line[0] - (y_top - tf)) <= inner_tol
            ):
                web_x_values.extend((line[1], line[2]))
        if web_x_values:
            material_x_lo = min(web_x_values)
            material_x_hi = max(web_x_values)
            # When the web band has no source edge at one end (its own taper is
            # drawn only on the flange inner lines, or a multi-view block's
            # connector line was filtered out), the web extends to the member
            # skeleton end -- it cannot hang short at an arbitrary taper end.
            skeleton_span = max(skeleton_hi - skeleton_lo, 1.0)
            end_gap = max(2.0, 0.08 * skeleton_span)
            if material_x_hi < skeleton_hi - end_gap:
                material_x_hi = skeleton_hi
            if material_x_lo > skeleton_lo + end_gap:
                material_x_lo = skeleton_lo
            window = max(3.0 * tf, 80.0)
            x_lo, x_hi, left_hits, right_hits = extents_with_end_window(
                material_x_lo, material_x_hi,
                y_bottom + tf, y_top - tf,
                window=window,
                coverage_floor=0.12 * web_span,
                include_horizontal=False,
                exclude_outside_band=True,
            )
            plates[BoardRole.WEB.value] = (x_lo, x_hi)
            evidence[BoardRole.WEB.value] = (
                f"腹板源边材料区间[{material_x_lo:.1f},{material_x_hi:.1f}]"
                f"+端部封口({left_hits}左/{right_hits}右)"
            )
        else:
            plates[BoardRole.WEB.value] = (front.s_min, front.s_max)
            evidence[BoardRole.WEB.value] = "腹板带无源边，保守回退主视图边界"

        return plates, evidence

    @staticmethod
    def _safe_integer(value: float) -> int:
        bounded = max(0.0, value)
        nearest = round(bounded)
        representation_tolerance = 1e-12 * max(1.0, abs(bounded))
        if abs(bounded - nearest) <= representation_tolerance:
            bounded = float(nearest)
        return floor(bounded)

    def _step3_read_horizontal_setbacks(
        self,
        front: ViewCandidate,
        top: ViewCandidate | None,
        spec: BoxSpec,
        plates: dict[str, tuple[float, float]],
        evidence: dict[str, str],
        scale_to_mm: float,
        dimension_values: Sequence[float],
    ) -> tuple[list[PlateMeasurement], float, list[str]]:
        warnings: list[str] = []
        # Dynamic confidence: depth match drives the baseline, front/top length
        # agreement and setback corroboration add evidence.
        depth_error = abs(front.height * scale_to_mm - spec.depth) / max(spec.depth, 1.0)
        confidence = 0.66 + 0.16 * exp(-depth_error / max(self.config.spec_dimension_tolerance_ratio, 1e-6))
        if top is not None:
            length_difference = abs(top.length - front.length) * scale_to_mm
            if length_difference > max(2.0, 0.003 * front.length * scale_to_mm):
                warnings.append("主视图与俯视图纵向包围长度不同；左右进以主视图为准")
            else:
                confidence += 0.08
        confidence = max(0.0, min(1.0, confidence))

        # The global horizontal reference is the union of every plate's own
        # source edges.  Never the raw view bbox: end-fillet arcs and, in a
        # packed multi-view block, connector lines to a neighbour view would
        # otherwise inflate X_left/X_right.
        view_x_left = min(x_lo for x_lo, x_hi in plates.values())
        view_x_right = max(x_hi for x_lo, x_hi in plates.values())

        def values(left: float, right: float) -> tuple[float, float, int, int]:
            left_raw = max(0.0, left - view_x_left) * scale_to_mm
            right_raw = max(0.0, view_x_right - right) * scale_to_mm
            return (
                left_raw,
                right_raw,
                self._safe_integer(left_raw),
                self._safe_integer(right_raw),
            )

        measurements: list[PlateMeasurement] = []
        for role, (x_lo, x_hi) in plates.items():
            left_raw, right_raw, left_safe, right_safe = values(x_lo, x_hi)
            extra = ""
            if left_raw > 0 or right_raw > 0:
                nearest = min(
                    dimension_values,
                    key=lambda value: abs(value - max(left_raw, right_raw)),
                    default=None,
                )
                if (
                    nearest is not None
                    and abs(nearest - max(left_raw, right_raw))
                    <= self.config.dimension_corroboration_tolerance_mm
                ):
                    extra = f"；与尺寸文字 {nearest:g} mm 相互佐证"
            measurements.append(PlateMeasurement(
                role=role,
                left_raw=left_raw,
                right_raw=right_raw,
                left_safe=left_safe,
                right_safe=right_safe,
                confidence=confidence,
                evidence=evidence[role] + extra,
            ))
        return measurements, confidence, warnings

    @staticmethod
    def _plates_equivalent(m1: PlateMeasurement, m2: PlateMeasurement, tolerance_mm: float = 2.0) -> bool:
        """Approximate the splitter's `plates_equivalent` by identical setbacks:
        same left/right setback => same cut length and same plate identity."""
        return (
            abs(m1.left_raw - m2.left_raw) <= tolerance_mm
            and abs(m1.right_raw - m2.right_raw) <= tolerance_mm
        )

    def _finalize_measurements(
        self,
        measurements: list[PlateMeasurement],
        top_webs: dict[str, object] | None,
    ) -> list[PlateMeasurement]:
        """Merge upper/lower flange and upper/lower web when their setbacks are
        identical, exactly like the splitter's equivalence grouping:
        WEB_LEFT+WEB_RIGHT -> 腹(×2), FLANGE_TOP+FLANGE_BOTTOM -> 翼(×2);
        otherwise they stay as 上腹/下腹 and 上翼/下翼."""
        by_role = {m.role: m for m in measurements}
        result: list[PlateMeasurement] = []

        upper = by_role.get("上翼")
        lower = by_role.get("下翼")
        if (
            upper is not None
            and lower is not None
            and self._plates_equivalent(upper, lower)
        ):
            result.append(PlateMeasurement(
                "翼",
                min(upper.left_raw, lower.left_raw),
                min(upper.right_raw, lower.right_raw),
                min(upper.left_safe, lower.left_safe),
                min(upper.right_safe, lower.right_safe),
                min(upper.confidence, lower.confidence),
                "上翼/下翼左右进相同，合并为翼(×2)（拆板 FLANGE_TOP+FLANGE_BOTTOM）",
            ))
        else:
            if upper is not None:
                result.append(upper)
            if lower is not None:
                result.append(lower)

        if top_webs is not None:
            up_info = top_webs.get("上腹")
            low_info = top_webs.get("下腹")
            if up_info is not None and low_info is not None:
                def web_measurement(role: str, info: object, source: str) -> PlateMeasurement:
                    left_raw = float(info["left_offset_mm"])
                    right_raw = float(info["right_offset_mm"])
                    return PlateMeasurement(
                        role,
                        left_raw,
                        right_raw,
                        self._safe_integer(left_raw),
                        self._safe_integer(right_raw),
                        0.80,
                        source,
                    )

                upper_web = web_measurement("上腹", up_info, "俯视图下侧腹板(WEB_LEFT 上腹)")
                lower_web = web_measurement("下腹", low_info, "俯视图上侧腹板(WEB_RIGHT 下腹)")
                if self._plates_equivalent(upper_web, lower_web):
                    # Upper/lower web identical: the front-view web band source
                    # (clipped, material boundary) is more accurate than the top
                    # view's outer-surface reading, so prefer it as 腹(×2).
                    front_web = by_role.get("腹板")
                    if front_web is not None:
                        result.append(PlateMeasurement(
                            "腹",
                            front_web.left_raw,
                            front_web.right_raw,
                            front_web.left_safe,
                            front_web.right_safe,
                            front_web.confidence,
                            front_web.evidence + "；上腹/下腹相同，合并为腹(×2)（拆板 WEB_LEFT+WEB_RIGHT）",
                        ))
                    else:
                        result.append(PlateMeasurement(
                            "腹",
                            min(upper_web.left_raw, lower_web.left_raw),
                            min(upper_web.right_raw, lower_web.right_raw),
                            min(upper_web.left_safe, lower_web.left_safe),
                            min(upper_web.right_safe, lower_web.right_safe),
                            0.80,
                            "上腹/下腹相同，合并为腹(×2)",
                        ))
                else:
                    result.append(upper_web)
                    result.append(lower_web)
                return result

        web = by_role.get("腹板")
        if web is not None:
            result.append(PlateMeasurement(
                "腹",
                web.left_raw,
                web.right_raw,
                web.left_safe,
                web.right_safe,
                web.confidence,
                web.evidence + "；主视图腹板带源边合并为腹(×2)",
            ))
        return result

    # ------------------------------------------------------------------
    # 顶层流程
    # ------------------------------------------------------------------

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

        if spec is None:
            return DrawingResult(
                drawing.path.name,
                part_number,
                "",
                "ERROR_BOX_SPEC_NOT_FOUND",
                0.0,
                [],
                warnings + ["未识别到 BOX 截面规格，禁止推测翼厚和腹厚"],
            )

        front, top, view_warnings = self._step1_locate_main_view(drawing, spec)
        warnings.extend(view_warnings)
        if front is None:
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text,
                "ERROR_VIEW_NOT_FOUND",
                0.0,
                [],
                warnings,
            )

        scale_to_mm = front.unit_scale_to_mm
        # Unit verification: DXF $INSUNITS must be millimetres and geometry must
        # reproduce the declared depth under that scale.
        unit_status = self._unit_diagnostics(front, spec, drawing)
        if unit_status.get("status") != "verified_mm":
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text,
                "ERROR_UNIT_SCALE_UNVERIFIED",
                0.0,
                [],
                warnings + [
                    "DXF坐标到毫米的比例无法由 $INSUNITS 与 BOX 深度可靠验证；为防止等比例缩放导致错误，已停止输出左右进"
                ],
                diagnostics=unit_status,
            )

        # 折线（多段弯折）构件（如 h-4 系列）：板件端部读取需要折线路径追踪，
        # 读取器不支持自动左右进 —— 输出失败状态，Excel 第二阶段走人工补录
        # 标红路径（BOX读取失败需补录），由人工处理而非错误读数。
        if self._cranked_section_height(front, spec) is not None:
            return DrawingResult(
                drawing.path.name,
                part_number,
                spec.raw_text,
                "ERROR_CRANKED_UNSUPPORTED",
                0.0,
                [],
                warnings + ["该构件为多段弯折（折线）构件，系统无法自动读取左右进，请在 Excel 第二阶段人工补录"],
            )

        plates, evidence = self._step2_identify_plates(front, top, spec)
        measurements, confidence, measure_warnings = self._step3_read_horizontal_setbacks(
            front,
            top,
            spec,
            plates,
            evidence,
            scale_to_mm,
            self._dimension_values(drawing.texts),
        )
        warnings.extend(measure_warnings)

        view_x_left = min(x_lo for x_lo, x_hi in plates.values())
        view_x_right = max(x_hi for x_lo, x_hi in plates.values())

        # BOX 有上腹/下腹两块腹板（拆板 WEB_LEFT/WEB_RIGHT）。主视图正面投影重叠，
        # 主输出合并为"腹板"（与拆板"腹"成对合并对齐）；俯视图可从两侧条带读出
        # 各自边界，供需要区分时使用（上腹=俯视图一侧、下腹=另一侧，对齐主视图）。
        top_webs: dict[str, object] | None = None
        if (
            top is not None
            and abs(front.length - top.length) <= max(3.0, 0.01 * front.length)
        ):
            tw = spec.web_thickness
            v_lo, v_hi = top.t_min, top.t_max
            shift = view_x_left - top.s_min
            top_hlines = self._key_horizontal_lines(top.primitives, top.length)
            if top_hlines:
                t_skel_lo = min(line[1] for line in top_hlines)
                t_skel_hi = max(line[2] for line in top_hlines)
            else:
                t_skel_lo, t_skel_hi = top.s_min, top.s_max
            # The web band's own horizontal lines may be split into short Tekla
            # segments at a sloped end; every horizontal line extends the
            # skeleton so a real web end is never mistaken for a connector line.
            for primitive in top.primitives:
                y_min, y_max, x_lo, x_hi = self._primitive_geom(primitive)
                if y_max - y_min <= 0.5:
                    t_skel_lo = min(t_skel_lo, x_lo)
                    t_skel_hi = max(t_skel_hi, x_hi)
            t_margin = max(3.0 * tw, 80.0)

            def top_band(ylo: float, yhi: float) -> tuple[float, float] | None:
                xs: list[float] = []
                for primitive in top.primitives:
                    y_min, y_max, x_lo, x_hi = self._primitive_geom(primitive)
                    center = 0.5 * (x_lo + x_hi)
                    if not (
                        t_skel_lo - t_margin <= center <= t_skel_hi + t_margin
                    ) and y_max - y_min <= 0.6 * (v_hi - v_lo):
                        continue
                    mid_y = 0.5 * (y_min + y_max)
                    if not (ylo - 1.0 <= mid_y <= yhi + 1.0):
                        continue
                    if y_max - y_min <= 0.5 or min(y_max, yhi) - max(y_min, ylo) >= 0.5 * (yhi - ylo):
                        xs.extend(point[0] + shift for point in primitive.points)
                return (min(xs), max(xs)) if xs else None

            # 上下腹板配对：上腹/下腹均从俯视图带读取（原逻辑）。
            # 注意：拆板对"台阶端"（如 3t1 系，翼板内表面斜切）判定两块相同
            # （合并），对"真实坡口"（如 w4e 系，web_left 端部坡口 20 mm）
            # 判定拆分；两者在主/俯视图竖线结构上完全同构，读取器在可观测
            # 特征上无法区分，故保持保守合并（与拆板"两块相同"的图一致）。
            upper_web = top_band(v_lo, v_lo + tw)
            lower_web = top_band(v_hi - tw, v_hi)
            if upper_web is not None and lower_web is not None:
                top_webs = {
                    "上腹": {
                        "left_x_dxf": upper_web[0],
                        "right_x_dxf": upper_web[1],
                        "left_offset_mm": max(0.0, (upper_web[0] - view_x_left) * scale_to_mm),
                        "right_offset_mm": max(0.0, (view_x_right - upper_web[1]) * scale_to_mm),
                    },
                    "下腹": {
                        "left_x_dxf": lower_web[0],
                        "right_x_dxf": lower_web[1],
                        "left_offset_mm": max(0.0, (lower_web[0] - view_x_left) * scale_to_mm),
                        "right_offset_mm": max(0.0, (view_x_right - lower_web[1]) * scale_to_mm),
                    },
                }

        # 与拆板输出对齐：上下翼/上下腹左右进相同合并"翼"/"腹"（×2），不同分别输出。
        measurements = self._finalize_measurements(measurements, top_webs)

        diagnostics: dict[str, object] = {
            "front_view": {
                "block": front.view_id,
                "left_x_dxf": view_x_left,
                "right_x_dxf": view_x_right,
                "raw_left_x_dxf": front.s_min,
                "raw_right_x_dxf": front.s_max,
                "length_mm": (view_x_right - view_x_left) * scale_to_mm,
                "height_mm": front.height * scale_to_mm,
            },
            "top_view": None if top is None else {
                "block": top.view_id,
                "left_x_dxf": top.s_min,
                "right_x_dxf": top.s_max,
            },
            "plates": {
                role: {
                    "left_x_dxf": x_lo,
                    "right_x_dxf": x_hi,
                    "left_offset_mm": max(0.0, (x_lo - view_x_left) * scale_to_mm),
                    "right_offset_mm": max(0.0, (view_x_right - x_hi) * scale_to_mm),
                }
                for role, (x_lo, x_hi) in plates.items()
            },
            "top_webs": top_webs,
            "unit": unit_status,
        }

        return DrawingResult(
            drawing.path.name,
            part_number,
            spec.raw_text,
            "OK",
            confidence,
            measurements,
            warnings,
            diagnostics,
        )

    def _cranked_section_height(
        self,
        view: ViewCandidate,
        spec: BoxSpec,
    ) -> float | None:
        """折线（多段弯折）构件内部截面高度。

        整块包围盒高度被斜段拉大（h-4 系列 2365 vs H=800），但每一折线段的
        顶底线 y 差仍 = BOX 深度 H；返回该截面高度，否则 None。
        """
        tol = self.config.view_height_tolerance_mm / max(view.unit_scale_to_mm, 1e-9)
        # 只有当块包围盒高度无法匹配 H 与 W 时才是折线构件（折线的斜段必然
        # 拉大块高）；正常构件的块高 = H 或 W，其顶底线对 y 差也恰为 H/W，
        # 若在此检测线对会把正常构件误判为折线（如 2b1-cb-92 高 800=W）。
        if (
            abs(view.height - spec.depth) <= max(tol, 1.5 * tol)
            or abs(view.height - spec.width) <= max(tol, 1.5 * tol)
        ):
            return None
        line_ys = sorted({
            round(0.5 * (segment.a[1] + segment.b[1]), 1)
            for segment in view.segments
            if abs(segment.b[1] - segment.a[1])
            <= self.config.flange_band_tolerance_mm
            and abs(segment.b[0] - segment.a[0]) > 0.3 * max(view.length, 1.0)
        })
        for y1, y2 in combinations(line_ys, 2):
            span = abs(y2 - y1)
            if abs(span - spec.depth) <= max(tol, 1.5 * tol):
                return span
        return None

    def _unit_diagnostics(
        self,
        front: ViewCandidate,
        spec: BoxSpec,
        drawing: DrawingData,
    ) -> dict[str, object]:
        scale = front.unit_scale_to_mm
        header_scale = drawing.header_unit_to_mm
        checks: list[dict[str, object]] = []
        if header_scale is not None and header_scale > 0:
            checks.append({
                "source": "dxf_header_insunits",
                "selected_mm_per_unit": scale,
                "reference_mm_per_unit": header_scale,
                "relative_error": abs(scale - header_scale) / header_scale,
            })
        depth_error = abs(front.height * scale - spec.depth) / max(spec.depth, 1.0)
        if depth_error > self.config.unit_verification_tolerance_ratio:
            # 折线构件：整块高度被斜段拉大，用内部截面高度做深度验证
            section = self._cranked_section_height(front, spec)
            if section is not None:
                depth_error = abs(section * scale - spec.depth) / max(spec.depth, 1.0)
        checks.append({
            "source": "box_depth_mm",
            "geometry_bbox_mm": front.height * scale,
            "reference_mm": spec.depth,
            "relative_error": depth_error,
        })
        width_error = abs(front.height * scale - spec.width) / max(spec.width, 1.0)
        checks.append({
            "source": "box_width_mm",
            "geometry_bbox_mm": front.height * scale,
            "reference_mm": spec.width,
            "relative_error": width_error,
        })

        header_matches = (
            header_scale is not None
            and header_scale > 0
            and abs(scale - header_scale) / header_scale
            <= self.config.unit_verification_tolerance_ratio
        )
        depth_ok = depth_error <= self.config.unit_verification_tolerance_ratio
        width_ok = width_error <= self.config.unit_verification_tolerance_ratio
        result: dict[str, object] = {
            "output_unit": "mm",
            "coordinate_unit_to_mm": scale,
            "status": "unverified",
            "header_insunits_code": drawing.insunits_code,
            "header_insunits_name": drawing.insunits_name,
            "header_unit_to_mm": header_scale,
            "checks": checks,
        }
        if header_matches and (depth_ok or width_ok):
            result["status"] = "verified_mm"
            result["verification_mode"] = "header_plus_depth"
        elif depth_ok and width_ok:
            result["status"] = "verified_mm"
            result["verification_mode"] = "depth_and_width"
        else:
            result["reason"] = (
                "insufficient independent unit evidence "
                f"(header={header_scale!r}, depth_error={depth_error:.4g}, "
                f"width_error={width_error:.4g})"
            )
        return result
