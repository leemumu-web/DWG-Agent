from __future__ import annotations

from math import ceil
from pathlib import Path
from typing import Iterable

from .analyzer import BHAnalyzer
from .model import DrawingData, DrawingResult, PlateMeasurement, ViewCandidate


def _configure_cjk_font() -> None:
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    preferred = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans SC",
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
    ]
    installed = {font.name for font in font_manager.fontManager.ttflist}
    for name in preferred:
        if name in installed:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


def _xy_mm(front: ViewCandidate, point: tuple[float, float]) -> tuple[float, float]:
    scale = front.unit_scale_to_mm
    return (point[0] - front.s_min) * scale, (point[1] - front.t_min) * scale


def _clip_segment_to_piece_x(
    segment,
    left: float,
    right: float,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return only the source-edge portion owned by one physical plate."""
    ax, ay = segment.a
    bx, by = segment.b
    dx = bx - ax
    if abs(dx) <= 1e-12:
        if left - 1e-9 <= ax <= right + 1e-9:
            return segment.a, segment.b
        return None
    u_left = (left - ax) / dx
    u_right = (right - ax) / dx
    u0 = max(0.0, min(u_left, u_right))
    u1 = min(1.0, max(u_left, u_right))
    if u1 < u0 - 1e-12:
        return None
    return (
        (ax + dx * u0, ay + (by - ay) * u0),
        (ax + dx * u1, ay + (by - ay) * u1),
    )


def _plot_flange_pieces(
    axis,
    front: ViewCandidate,
    pieces: Iterable[dict[str, object]],
    *,
    label: str,
) -> None:
    xs: list[float] = []
    ys: list[float] = []
    for piece in pieces:
        left = float(piece["left_x_dxf"])
        right = float(piece["right_x_dxf"])
        ids = piece.get("selected_segment_ids") or []
        for segment_id in sorted(set(int(value) for value in ids)):
            if not (0 <= segment_id < len(front.segments)):
                continue
            clipped = _clip_segment_to_piece_x(front.segments[segment_id], left, right)
            if clipped is None:
                continue
            a = _xy_mm(front, clipped[0])
            b = _xy_mm(front, clipped[1])
            xs.extend((a[0], b[0], float("nan")))
            ys.extend((a[1], b[1], float("nan")))
    if xs:
        axis.plot(xs, ys, linestyle="-", linewidth=2.4, label=label)


def _measurement_bounds_mm(
    measurement: PlateMeasurement,
    front_length_mm: float,
) -> tuple[float, float]:
    return measurement.left_raw, front_length_mm - measurement.right_raw


def render_three_step_sample(
    drawing: DrawingData,
    result: DrawingResult,
    analyzer: BHAnalyzer,
    output_path: Path,
) -> Path:
    """Render an auditable setback sample in true millimetre proportions.

    Solid lines show original/recognized geometry. Dashed horizontal arrows show
    the final left/right setbacks. The geometry axes use equal X/Y scale, so a
    tapered or scaled member is not visually distorted.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    _configure_cjk_font()
    spec = analyzer._extract_spec(drawing.texts)
    front, _, _ = analyzer._step1_locate_main_view(drawing, spec)
    if front is None:
        raise ValueError(f"cannot render view: {drawing.path.name}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    scale = front.unit_scale_to_mm
    width_mm = max(front.length * scale, 1.0)
    height_mm = max(front.height * scale, 1.0)
    margin_y = max(0.07 * height_mm, 0.006 * width_mm, 20.0)
    lane_step = max(0.10 * height_mm, 0.018 * width_mm, 42.0)
    lane_base = height_mm + 1.35 * margin_y
    annotation_top = lane_base + max(0, len(result.measurements) - 1) * lane_step

    # One canvas only: the image is the main view plus its measurement marks.
    # Full warnings, units and provenance belong in the JSON/Excel outputs,
    # not in an image intended for fast visual verification.
    figure_height = max(
        3.4,
        min(8.8, 17.0 * (annotation_top + 0.65 * lane_step) / width_mm + 0.7),
    )
    fig, axis = plt.subplots(figsize=(18.0, figure_height))

    # Original main-view geometry in transformed millimetres.
    source_x: list[float] = []
    source_y: list[float] = []
    for segment in front.segments:
        a = _xy_mm(front, segment.a)
        b = _xy_mm(front, segment.b)
        source_x.extend((a[0], b[0], float("nan")))
        source_y.extend((a[1], b[1], float("nan")))
    axis.plot(source_x, source_y, linestyle="-", linewidth=0.75, label="原始主视图实线")

    # Step 1: true horizontal boundaries.
    axis.plot(
        [0.0, 0.0, float("nan"), width_mm, width_mm],
        [-margin_y, height_mm + margin_y, float("nan"), -margin_y, height_mm + margin_y],
        linestyle="-",
        linewidth=2.1,
        label="主视图左右边界",
    )
    axis.text(0.0, height_mm + margin_y, "V_L", ha="center", va="bottom", fontsize=8.5, fontweight="bold")
    axis.text(width_mm, height_mm + margin_y, "V_R", ha="center", va="bottom", fontsize=8.5, fontweight="bold")

    diagnostics = result.diagnostics
    flange = diagnostics.get("flange_analysis") or {}
    plate = diagnostics.get("plate_identification") or {}

    # Step 2: overlay the actual source segments chosen for each flange. This is
    # critical for tapered BH: the upper flange remains visibly sloped instead
    # of being replaced by an artificial horizontal band.
    lower_diag = flange.get("lower") or {}
    upper_diag = flange.get("upper") or {}
    _plot_flange_pieces(
        axis,
        front,
        lower_diag.get("pieces") or [],
        label="下翼实际轮廓",
    )
    _plot_flange_pieces(
        axis,
        front,
        upper_diag.get("pieces") or [],
        label="上翼实际轮廓",
    )

    # Web horizontal boundaries are taken from the final millimetre offsets.
    web_measurement = next((item for item in result.measurements if item.role == "腹"), None)
    if web_measurement is not None:
        web_left, web_right = _measurement_bounds_mm(web_measurement, width_mm)
        axis.plot(
            [web_left, web_left, float("nan"), web_right, web_right],
            [0.20 * height_mm, 0.80 * height_mm, float("nan"), 0.20 * height_mm, 0.80 * height_mm],
            linestyle="-",
            linewidth=2.2,
            label="腹板水平边界",
        )

    # Step 3: dashed horizontal dimensions in mm.
    for index, measurement in enumerate(result.measurements):
        plate_left, plate_right = _measurement_bounds_mm(measurement, width_mm)
        y_lane = lane_base + index * lane_step

        def draw_side(start: float, end: float, raw: float, safe: int, side_name: str) -> None:
            if raw <= 0.05:
                x_value = start
                axis.plot([x_value, x_value], [height_mm, y_lane], linestyle="--", linewidth=1.0)
                alignment = "left" if side_name == "左" else "right"
                x_offset = max(0.004 * width_mm, 4.0) * (1.0 if side_name == "左" else -1.0)
                axis.text(
                    x_value + x_offset,
                    y_lane,
                    f"{measurement.role}{side_name}进：安全 0 mm（原始 0.000 mm）",
                    ha=alignment,
                    va="bottom",
                    fontsize=7.8,
                    bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.78, "pad": 0.8},
                )
                return
            axis.plot(
                [start, start, float("nan"), end, end],
                [height_mm, y_lane, float("nan"), height_mm, y_lane],
                linestyle="--",
                linewidth=1.0,
            )
            axis.annotate(
                "",
                xy=(end, y_lane),
                xytext=(start, y_lane),
                arrowprops={"arrowstyle": "<->", "linestyle": "--", "linewidth": 1.2},
            )
            axis.text(
                0.5 * (start + end),
                y_lane + 0.10 * lane_step,
                f"{measurement.role}{side_name}进：安全 {safe} mm（原始 {raw:.3f} mm）",
                ha="center",
                va="bottom",
                fontsize=7.8,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.78, "pad": 0.8},
            )

        draw_side(0.0, plate_left, measurement.left_raw, measurement.left_safe, "左")
        draw_side(plate_right, width_mm, measurement.right_raw, measurement.right_safe, "右")

    # Step 2 detail: show every physical flange piece.  A plate boundary is
    # based on the material-bearing X interval, not on arbitrary internal lines.
    for side_key, y_center, side_label in (
        ("upper", 0.92 * height_mm, "上翼"),
        ("lower", 0.08 * height_mm, "下翼"),
    ):
        side_diag = flange.get(side_key) or {}
        pieces = side_diag.get("pieces") or []
        for piece in pieces:
            left_x = float(piece.get("left_offset_mm", 0.0))
            right_x = width_mm - float(piece.get("right_offset_mm", 0.0))
            band = max(0.025 * height_mm, 8.0)
            axis.plot(
                [left_x, left_x, float("nan"), right_x, right_x],
                [y_center - band, y_center + band, float("nan"), y_center - band, y_center + band],
                linestyle="-", linewidth=1.8,
                label="翼板实体左右边界" if side_key == "upper" and int(piece.get("index", 1)) == 1 else None,
            )
            axis.text(
                0.5 * (left_x + right_x), y_center + 1.25 * band,
                f"{side_label}-{int(piece.get('index', 1))}" if len(pieces) > 1 else side_label,
                ha="center", va="bottom", fontsize=7.6, fontweight="bold",
            )

    max_lane = lane_base + max(0, len(result.measurements) - 1) * lane_step
    axis.set_xlim(-0.025 * width_mm, 1.025 * width_mm)
    axis.set_ylim(-1.2 * margin_y, max(height_mm + margin_y, max_lane + 0.45 * lane_step))
    axis.set_aspect("equal", adjustable="box")
    unit_status = (diagnostics.get("units") or {}).get("status", "")
    axis.text(
        0.995,
        0.015,
        "单位校验：通过（mm）" if unit_status == "verified_mm" else "单位校验：未通过",
        transform=axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=7.2,
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.82, "pad": 0.8},
    )
    handles, labels = axis.get_legend_handles_labels()
    unique: dict[str, object] = {}
    for handle, label in zip(handles, labels, strict=False):
        if label and label not in unique:
            unique[label] = handle
    if unique:
        axis.legend(
            unique.values(),
            unique.keys(),
            loc="upper center",
            bbox_to_anchor=(0.5, -0.015),
            ncol=min(3, len(unique)),
            fontsize=7.2,
            frameon=True,
            framealpha=0.92,
            borderpad=0.45,
            handlelength=2.4,
            columnspacing=1.2,
        )
    axis.set_axis_off()
    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.15)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return output_path


def build_contact_sheets(
    image_paths: Iterable[Path],
    output_dir: Path,
    prefix: str = "左右进样例汇总",
    rows_per_sheet: int = 4,
) -> list[Path]:
    from PIL import Image

    paths = list(image_paths)
    if not paths:
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    target_width = 1900
    margin = 24
    result: list[Path] = []
    for sheet_index in range(ceil(len(paths) / rows_per_sheet)):
        subset = paths[sheet_index * rows_per_sheet : (sheet_index + 1) * rows_per_sheet]
        images: list[Image.Image] = []
        for path in subset:
            image = Image.open(path).convert("RGB")
            new_height = max(1, int(image.height * target_width / image.width))
            images.append(image.resize((target_width, new_height)))
        page_height = sum(image.height for image in images) + margin * (len(images) + 1)
        page = Image.new("RGB", (target_width + 2 * margin, page_height), "white")
        y_value = margin
        for image in images:
            page.paste(image, (margin, y_value))
            y_value += image.height + margin
        path = output_dir / f"{prefix}_{sheet_index + 1:02d}.png"
        page.save(path)
        result.append(path)
    return result
