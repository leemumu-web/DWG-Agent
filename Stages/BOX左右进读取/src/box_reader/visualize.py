from __future__ import annotations

from pathlib import Path

from .analyzer import BoxAnalyzer
from .model import DrawingData, DrawingResult, ViewCandidate

# ColorBrewer Set1 — high-discrimination hues for plate roles, kept distinct
# from the neutral member contour and the blue view bounds.
# Roles mirror the splitter output: 翼/腹 merged, 上翼/下翼/上腹/下腹 split.
_ROLE_COLORS = {
    "上翼": "#e41a1c",
    "翼": "#e41a1c",
    "下翼": "#4daf4a",
    "上腹": "#984ea3",
    "腹": "#984ea3",
    "腹板": "#984ea3",
    "下腹": "#ff7f00",
}
_VIEW_BOUND_COLOR = "#377eb8"
_CONTOUR_COLOR = "#555555"
_LANE_LINE_COLOR = "#999999"


def _configure_cjk_font() -> None:
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    preferred = [
        "Noto Sans CJK SC",
        "WenQuanYi Zen Hei",
        "WenQuanYi Micro Hei",
        "Source Han Sans SC",
        "Microsoft YaHei",
        "PingFang SC",
        "Heiti SC",
        "SimHei",
    ]
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in preferred:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False


def _xy_mm(view: ViewCandidate, point: tuple[float, float]) -> tuple[float, float]:
    return (
        (point[0] - view.s_min) * view.unit_scale_to_mm,
        (point[1] - view.t_min) * view.unit_scale_to_mm,
    )


def _measurement_bounds_mm(
    measurement,
    width_mm: float,
    scale: float,
) -> tuple[float, float]:
    left = measurement.left_raw / scale
    right = measurement.right_raw / scale
    return (left, width_mm - right)


def render_box_sample(
    drawing: DrawingData,
    result: DrawingResult,
    analyzer: BoxAnalyzer,
    output_path: Path,
) -> Path:
    """Render an auditable BOX setback sample in true millimetre proportions.

    Layout (top to bottom):
      * title bar with part number / spec / status / unit verdict
      * the main-view Part geometry at 1:1
      * coloured vertical plate-boundary marks at each plate's true y band
      * one setback lane per plate with <-> arrows for left/right setbacks
      * legend
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _configure_cjk_font()
    spec = analyzer._extract_spec(drawing.texts)
    front, _, _ = analyzer._step1_locate_main_view(drawing, spec)
    if front is None:
        raise ValueError(f"cannot render view: {drawing.path.name}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    scale = front.unit_scale_to_mm
    width_mm = max(front.length * scale, 1.0)
    height_mm = max(front.height * scale, 1.0)
    tf = spec.flange_thickness if spec else 0.0

    # Long members (width >> depth) must stay readable: keep a sane minimum
    # canvas height and scale annotation size with the drawing span.
    is_long = width_mm > 5.0 * max(height_mm, 1.0)
    margin_y = max(0.07 * height_mm, 0.006 * width_mm, 20.0)
    lane_step = max(0.12 * height_mm, (0.022 if is_long else 0.018) * width_mm, 48.0)
    lane_base = height_mm + 1.5 * margin_y
    lane_top = lane_base + max(0, len(result.measurements) - 1) * lane_step
    annotation_top = lane_top + 0.55 * lane_step
    title_height = max(36.0, 0.10 * height_mm)
    base_font = 8.4 + min(2.0, width_mm / 4000.0)
    contour_lw = 0.9 if is_long else 0.8

    figure_height = max(
        5.0 if is_long else 4.2,
        min(9.4, 17.0 * (annotation_top + title_height + 0.4 * margin_y) / width_mm + 0.7),
    )
    fig, axis = plt.subplots(figsize=(18.0, figure_height))

    # ---- Step 1: main-view Part geometry (dark solid) ----
    source_x: list[float] = []
    source_y: list[float] = []
    for segment in front.segments:
        a = _xy_mm(front, segment.a)
        b = _xy_mm(front, segment.b)
        source_x.extend((a[0], b[0], float("nan")))
        source_y.extend((a[1], b[1], float("nan")))
    axis.plot(source_x, source_y, linestyle="-", linewidth=contour_lw, color=_CONTOUR_COLOR, label="原始主视图轮廓", zorder=2)

    # ---- True horizontal boundaries V_L / V_R ----
    axis.plot(
        [0.0, 0.0, float("nan"), width_mm, width_mm],
        [-margin_y, height_mm + margin_y, float("nan"), -margin_y, height_mm + margin_y],
        linestyle="-", linewidth=2.0, color=_VIEW_BOUND_COLOR, label="主视图左右边界", zorder=3,
    )
    axis.text(0.0, height_mm + margin_y, "V_L", ha="center", va="bottom", fontsize=9, fontweight="bold", color=_VIEW_BOUND_COLOR)
    axis.text(width_mm, height_mm + margin_y, "V_R", ha="center", va="bottom", fontsize=9, fontweight="bold", color=_VIEW_BOUND_COLOR)

    # ---- Step 2: plate boundary marks, one band per role, labels never overlap ----
    # The flange and web boundaries naturally sit together (BOX cross-section);
    # each output plate (merged or split) gets its own narrow y band so its role
    # label stays legible. Upper/lower webs occupy the upper/lower web band.
    web_span = max(height_mm - 2.0 * tf, 1.0)
    web_lo = tf
    band_h = 0.16 * web_span
    role_y_band = {
        "上翼": (max(height_mm - tf, 0.0), height_mm),
        "下翼": (0.0, min(tf, height_mm)),
        "翼": (max(height_mm - tf, 0.0), height_mm),
        "上腹": (web_lo + 0.58 * web_span - 0.5 * band_h, web_lo + 0.58 * web_span + 0.5 * band_h),
        "下腹": (web_lo + 0.42 * web_span - 0.5 * band_h, web_lo + 0.42 * web_span + 0.5 * band_h),
        "腹": (web_lo + 0.50 * web_span - 0.6 * band_h, web_lo + 0.50 * web_span + 0.6 * band_h),
        "腹板": (web_lo + 0.50 * web_span - 0.6 * band_h, web_lo + 0.50 * web_span + 0.6 * band_h),
    }
    for measurement in result.measurements:
        x_lo_mm = measurement.left_raw
        x_hi_mm = width_mm - measurement.right_raw
        y_lo, y_hi = role_y_band.get(measurement.role, (0.25 * height_mm, 0.75 * height_mm))
        color = _ROLE_COLORS.get(measurement.role, "#7f7f7f")
        role_label = f"{measurement.role}(上/下同)" if measurement.role in ("腹", "翼") else measurement.role
        axis.plot(
            [x_lo_mm, x_lo_mm, float("nan"), x_hi_mm, x_hi_mm],
            [y_lo, y_hi, float("nan"), y_lo, y_hi],
            linestyle="-", linewidth=2.4, color=color,
            label=f"{measurement.role}实体左右边界", zorder=4,
        )
        axis.text(
            0.5 * (x_lo_mm + x_hi_mm),
            0.5 * (y_lo + y_hi),
            role_label,
            ha="center", va="center", fontsize=8.5, fontweight="bold", color="#111111",
        )

    # ---- Step 3: setback lanes with arrows ----
    for index, measurement in enumerate(result.measurements):
        plate_left, plate_right = _measurement_bounds_mm(measurement, width_mm, scale)
        y_lane = lane_base + index * lane_step

        def draw_side(
            start: float,
            end: float,
            raw: float,
            safe: int,
            side_name: str,
            *,
            role: str = measurement.role,
            lane_y: float = y_lane,
        ) -> None:
            color = _ROLE_COLORS.get(role, "#555555")
            role_label = f"{role}(上/下同)" if role in ("腹", "翼") else role
            text_y = lane_y + 0.32 * lane_step
            if raw <= 0.05:
                axis.plot([start, start], [height_mm, lane_y], linestyle="--", linewidth=1.0, color=_LANE_LINE_COLOR)
                align = "left" if side_name == "左" else "right"
                x_offset = (1.0 if side_name == "左" else -1.0) * max(0.005 * width_mm, 6.0)
                axis.text(
                    start + x_offset, text_y,
                    f"{role_label}{side_name}进 0 mm",
                    ha=align, va="bottom", fontsize=base_font, color="#222222",
                    bbox={"facecolor": "white", "edgecolor": "#cccccc", "alpha": 0.92, "pad": 1.2, "linewidth": 0.6},
                )
                return
            axis.plot(
                [start, start, float("nan"), end, end],
                [height_mm, lane_y, float("nan"), height_mm, lane_y],
                linestyle="--", linewidth=1.0, color=_LANE_LINE_COLOR,
            )
            axis.annotate(
                "",
                xy=(end, lane_y),
                xytext=(start, lane_y),
                arrowprops={"arrowstyle": "<->", "linestyle": "--", "linewidth": 1.6, "color": color},
            )
            axis.text(
                0.5 * (start + end), text_y,
                f"{role_label}{side_name}进：{safe} mm",
                ha="center", va="bottom", fontsize=base_font, color="#222222",
                bbox={"facecolor": "white", "edgecolor": "#cccccc", "alpha": 0.92, "pad": 1.2, "linewidth": 0.6},
            )

        draw_side(0.0, plate_left, measurement.left_raw, measurement.left_safe, "左")
        draw_side(plate_right, width_mm, measurement.right_raw, measurement.right_safe, "右")

    # ---- Title bar ----
    unit = result.diagnostics.get("unit") or {}
    unit_status = unit.get("status", "")
    unit_text = "单位校验：通过（mm）" if unit_status == "verified_mm" else "单位校验：未通过"
    spec_text = spec.raw_text if spec else result.specification or "无规格"
    status_color = "#1f7a1f" if result.status == "OK" else "#b00020"
    # Title bar: part number + spec centred, member info left, status right.
    axis.text(
        0.50, 1.0, f"{result.part_number}    {spec_text}",
        transform=axis.transAxes, ha="center", va="top", fontsize=12, fontweight="bold", color="#111111",
    )
    axis.text(
        0.01, 0.955, f"主视图 {front.view_id}  全长 {width_mm:.0f} mm  深度 {height_mm:.0f} mm",
        transform=axis.transAxes, ha="left", va="top", fontsize=8.2, color="#444444",
    )
    axis.text(
        0.99, 0.955, f"{result.status}  置信度 {result.confidence:.2f}",
        transform=axis.transAxes, ha="right", va="top", fontsize=9.5, fontweight="bold", color=status_color,
    )
    axis.text(
        0.99, 0.915, unit_text,
        transform=axis.transAxes, ha="right", va="top", fontsize=8.2, color=_VIEW_BOUND_COLOR,
    )

    max_lane = lane_base + max(0, len(result.measurements) - 1) * lane_step
    axis.set_xlim(-0.025 * width_mm, 1.025 * width_mm)
    axis.set_ylim(-1.3 * margin_y, max(height_mm + margin_y, max_lane + 0.6 * lane_step, annotation_top))
    # True 1:1 millimetre proportions: the DXF scale is exact and must not be
    # distorted by figure layout, so the drawing stays aspect-equal.
    axis.set_aspect("equal", adjustable="box")

    handles, labels = axis.get_legend_handles_labels()
    unique: dict[str, object] = {}
    for handle, label in zip(handles, labels, strict=False):
        if label and label not in unique:
            unique[label] = handle
    if unique:
        legend = axis.legend(
            unique.values(),
            unique.keys(),
            loc="upper center",
            bbox_to_anchor=(0.5, -0.012),
            ncol=min(4, len(unique)),
            fontsize=8.0 if is_long else 7.6,
            frameon=True,
            framealpha=0.92,
            borderpad=0.45,
            handlelength=2.4,
            columnspacing=1.2,
        )
        for text in legend.get_texts():
            text.set_color("#222222")
    axis.set_axis_off()
    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.14)
    fig.savefig(output_path, dpi=200 if is_long else 180, bbox_inches="tight")
    plt.close(fig)
    return output_path
