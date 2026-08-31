#!/usr/bin/env python3
"""Generate one stable, two-column DXF review sheet for three batches.

The root-level ``*_result.json`` files are the only pairing authority.  Every
task contains exactly the source and the latest program result.  Geometry is
translated 1:1 only.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import ezdxf
from ezdxf import bbox
from ezdxf.math import BoundingBox


CELL_PAD_X = 240.0
CELL_PAD_Y = 240.0
CELL_LABEL_BAND = 220.0
CELL_GAP = 360.0
ROW_PAD = 180.0
ROW_HEADER_BAND = 280.0
ROW_GAP = 500.0
TEXT_HEIGHT = 110.0
META_APPID = "MERGE_META"
_DXF_HEADER_SCAN_BYTES = 65536
_PART_NUMBER_PATTERN = re.compile(r"(?:^|[-_])cb[-_](\d+)(?:$|[-_])", re.IGNORECASE)


@dataclass(frozen=True)
class Cell:
    role: str
    path: Path | None
    width: float
    height: float
    extmin_x: float
    extmin_y: float
    extmax_x: float
    extmax_y: float


@dataclass(frozen=True)
class Task:
    sequence: int
    display_name: str
    family: str
    route: str
    task_dir: str
    cells: tuple[Cell, ...]


def natural_key(value: str) -> tuple[object, ...]:
    """Sort digit runs numerically while keeping all text comparisons stable."""
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    )


def part_number_key(value: str) -> tuple[object, ...]:
    """Sort by the numeric ``cb-N`` part number before any drawing prefix."""
    match = _PART_NUMBER_PATTERN.search(value)
    if match is None:
        return (1, natural_key(value))
    return (0, int(match.group(1)), natural_key(value))


def read_json_payload(path: Path) -> object:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"无法识别结果清单编码: {path}")


def clean_display_name(item: dict[str, object]) -> str:
    raw = Path(str(item["task_dir"])).name
    return re.sub(r"^[0-9a-fA-F]{16}__", "", raw)


def _dxf_codepage(path: Path) -> str | None:
    """Read the legacy code-page marker without decoding the whole DXF."""
    payload = path.read_bytes()[:_DXF_HEADER_SCAN_BYTES]
    lines = payload.decode("ascii", errors="ignore").splitlines()
    for index, line in enumerate(lines[:-2]):
        if line.strip().upper() != "$DWGCODEPAGE":
            continue
        if lines[index + 1].strip() == "3":
            return lines[index + 2].strip().upper()
    return None


def dxf_read_encoding(path: Path) -> str | None:
    """Return an explicit encoding for legacy DXF headers when required."""
    codepage = _dxf_codepage(path)
    if codepage is None:
        return None
    normalized = re.sub(r"[^A-Z0-9]", "", codepage)
    if normalized in {"GB2312", "GB231280", "GBK", "ANSI936", "936"}:
        return "gbk"
    if normalized in {"UTF8", "UTF080"}:
        return "utf-8"
    return None


def read_dxf_document(path: Path) -> ezdxf.document.Drawing:
    """Read a DXF while preserving Chinese text from legacy GB2312 files."""
    encoding = dxf_read_encoding(path)
    if encoding is None:
        return ezdxf.readfile(path)
    return ezdxf.readfile(path, encoding=encoding)


def drawing_extents(path: Path) -> tuple[float, float, float, float]:
    doc = read_dxf_document(path)
    ext = bbox.extents(doc.modelspace(), fast=True)
    if ext is None or not ext.has_data:
        raise ValueError(f"DXF 没有可排版图形: {path}")
    min_x, min_y = float(ext.extmin.x), float(ext.extmin.y)
    max_x, max_y = float(ext.extmax.x), float(ext.extmax.y)
    if max_x <= min_x or max_y <= min_y:
        raise ValueError(f"DXF 图形包络无效: {path}")
    return min_x, min_y, max_x, max_y


def make_cell(role: str, value: object) -> Cell:
    if not value:
        raise ValueError(f"{role} 路径为空")
    path = Path(str(value))
    if not path.is_file():
        raise FileNotFoundError(f"{role} 文件不存在: {path}")
    min_x, min_y, max_x, max_y = drawing_extents(path)
    return Cell(
        role=role,
        path=path,
        width=max_x - min_x,
        height=max_y - min_y,
        extmin_x=min_x,
        extmin_y=min_y,
        extmax_x=max_x,
        extmax_y=max_y,
    )


def make_placeholder_cell(role: str) -> Cell:
    """Represent a proven absence of program output without inventing geometry."""
    return Cell(
        role=role,
        path=None,
        width=3200.0,
        height=1800.0,
        extmin_x=0.0,
        extmin_y=0.0,
        extmax_x=3200.0,
        extmax_y=1800.0,
    )


def load_tasks(result_json: Path) -> tuple[list[Task], dict[str, int]]:
    payload = read_json_payload(result_json)
    if not isinstance(payload, list):
        raise ValueError(f"结果清单顶层不是数组: {result_json}")

    selected: list[dict[str, object]] = []
    counts = {
        "total": len(payload),
        "auto_accepted": 0,
        "manual_review": 0,
        "failed": 0,
        "without_program_result": 0,
    }
    for item in payload:
        route = str(item.get("automation_route", ""))
        if route not in {"auto_accepted", "manual_review"}:
            counts["failed"] += 1
            counts["without_program_result"] += 1
            continue
        counts[route] += 1
        result_path = (
            item.get("production_clean")
            if route == "auto_accepted"
            else item.get("review_candidate")
        )
        if not result_path:
            counts["without_program_result"] += 1
            continue
        selected.append(item)

    selected.sort(
        key=lambda item: (
            part_number_key(clean_display_name(item)),
            str(item.get("task_dir", "")).casefold(),
        )
    )

    tasks: list[Task] = []
    for sequence, item in enumerate(selected, start=1):
        route = str(item["automation_route"])
        result_path = (
            item.get("production_clean")
            if route == "auto_accepted"
            else item.get("review_candidate")
        )
        cells = (
            make_cell("原图", item.get("input")),
            (
                make_cell("程序拆板结果", result_path)
                if result_path
                else make_placeholder_cell("程序未产出拆板结果")
            ),
        )
        tasks.append(
            Task(
                sequence=sequence,
                display_name=clean_display_name(item),
                family=str(item.get("family", "")),
                route=route,
                task_dir=str(item.get("task_dir", "")),
                cells=cells,
            )
        )
    return tasks, counts


def copy_resources(target: ezdxf.document.Drawing, source: ezdxf.document.Drawing) -> None:
    for ltype in source.linetypes:
        name = ltype.dxf.name
        if name in target.linetypes:
            continue
        try:
            target.linetypes.add(name, pattern=ltype.simplified_line_pattern())
        except Exception:
            pass
    for layer in source.layers:
        name = layer.dxf.name
        if name in target.layers:
            continue
        attrs = {
            "color": layer.dxf.get("color", 7),
            "linetype": layer.dxf.get("linetype", "CONTINUOUS"),
            "lineweight": layer.dxf.get("lineweight", -3),
        }
        try:
            target.layers.add(name, dxfattribs=attrs)
        except Exception:
            try:
                target.layers.add(name)
            except Exception:
                pass
    for style in source.styles:
        name = style.dxf.name
        if name in target.styles:
            continue
        try:
            new_style = target.styles.new(name=name)
            new_style.dxf.font = style.dxf.get("font", "")
            new_style.dxf.bigfont = style.dxf.get("bigfont", "")
        except Exception:
            pass


def copy_visible_entity(target, entity, dx: float, dy: float) -> int:
    """Flatten INSERT/DIMENSION visuals and translate every copied primitive."""
    entity_type = entity.dxftype()
    if entity_type in {"INSERT", "DIMENSION"}:
        try:
            virtual = list(entity.virtual_entities())
        except Exception as exc:
            raise ValueError(f"无法展开实体 {entity_type}") from exc
        if not virtual:
            if entity_type == "INSERT":
                block_name = entity.dxf.get("name")
                try:
                    block = entity.doc.blocks.get(block_name)
                    if len(block) == 0:
                        return 0
                except Exception:
                    pass
            raise ValueError(f"实体 {entity_type} 没有可复制的可见元素")
        return sum(copy_visible_entity(target, child, dx, dy) for child in virtual)
    try:
        clone = entity.copy()
    except Exception as exc:
        raise ValueError(f"无法复制实体 {entity_type}") from exc
    try:
        clone.translate(dx, dy, 0.0)
    except Exception as exc:
        raise ValueError(f"无法平移实体 {entity_type}") from exc
    try:
        target.add_entity(clone)
    except Exception as exc:
        raise ValueError(f"无法写入实体 {entity_type}") from exc
    return 1


def add_closed_frame(msp, points: Iterable[tuple[float, float]], layer: str, color: int):
    frame = msp.add_lwpolyline(list(points), close=True, dxfattribs={"layer": layer, "color": color})
    return frame


def add_label(msp, text: str, x: float, y: float, *, height: float = TEXT_HEIGHT, color: int = 7) -> None:
    entity = msp.add_text(
        text,
        dxfattribs={"height": height, "layer": "MERGE_LABEL", "style": "MERGE_CN", "color": color},
    )
    entity.set_placement((x, y))


def merge_batch(
    result_json: Path,
    output_dxf: Path,
    manifest_json: Path,
    *,
    start: int = 0,
    count: int | None = None,
) -> dict[str, object]:
    tasks, counts = load_tasks(result_json)
    tasks = tasks[start:] if count is None else tasks[start : start + count]
    if not tasks:
        raise ValueError(f"没有可生成的自动通过或人工审核任务: {result_json}")

    target = ezdxf.new("R2010", setup=True)
    target.units = ezdxf.units.MM
    target.header["$INSUNITS"] = 4
    if META_APPID not in target.appids:
        target.appids.add(META_APPID)
    for name, color in (("MERGE_ROW_FRAME", 8), ("MERGE_CELL_FRAME", 9), ("MERGE_LABEL", 7)):
        if name not in target.layers:
            target.layers.add(name, color=color)
    if "MERGE_CN" not in target.styles:
        style = target.styles.new("MERGE_CN")
        style.dxf.font = "msyh.ttf"
    msp = target.modelspace()

    y_top = 0.0
    manifest_tasks: list[dict[str, object]] = []
    for task in tasks:
        cell_widths = [cell.width + 2.0 * CELL_PAD_X for cell in task.cells]
        cell_heights = [cell.height + 2.0 * CELL_PAD_Y + CELL_LABEL_BAND for cell in task.cells]
        content_height = max(cell_heights)
        row_width = 2.0 * ROW_PAD + sum(cell_widths) + CELL_GAP * (len(task.cells) - 1)
        row_height = ROW_HEADER_BAND + 2.0 * ROW_PAD + content_height
        row_bottom = y_top - row_height
        row_frame = add_closed_frame(
            msp,
            ((0.0, y_top), (row_width, y_top), (row_width, row_bottom), (0.0, row_bottom)),
            "MERGE_ROW_FRAME",
            8,
        )
        row_frame.set_xdata(META_APPID, [(1000, f"ROW|{task.sequence}|{task.display_name}|{task.route}")])
        route_zh = "自动通过" if task.route == "auto_accepted" else "人工审核"
        add_label(
            msp,
            f"{task.sequence:03d}  {task.display_name}  {task.family}  {route_zh}",
            ROW_PAD,
            y_top - ROW_HEADER_BAND + 70.0,
            height=130.0,
            color=2 if task.route == "auto_accepted" else 1,
        )

        x_left = ROW_PAD
        manifest_cells: list[dict[str, object]] = []
        for cell_index, (cell, cell_width) in enumerate(zip(task.cells, cell_widths), start=1):
            cell_top = y_top - ROW_HEADER_BAND - ROW_PAD
            cell_bottom = cell_top - content_height
            cell_right = x_left + cell_width
            cell_frame = add_closed_frame(
                msp,
                ((x_left, cell_top), (cell_right, cell_top), (cell_right, cell_bottom), (x_left, cell_bottom)),
                "MERGE_CELL_FRAME",
                9,
            )
            cell_frame.set_xdata(
                META_APPID,
                [(1000, f"CELL|{task.sequence}|{cell_index}|{cell.role}")],
            )
            add_label(msp, cell.role, x_left + CELL_PAD_X, cell_top - 145.0, height=115.0, color=3)

            content_left = x_left + CELL_PAD_X
            content_right = cell_right - CELL_PAD_X
            content_top = cell_top - CELL_LABEL_BAND - CELL_PAD_Y
            content_bottom = cell_bottom + CELL_PAD_Y
            center_x = (content_left + content_right) / 2.0
            center_y = (content_top + content_bottom) / 2.0
            source_center_x = (cell.extmin_x + cell.extmax_x) / 2.0
            source_center_y = (cell.extmin_y + cell.extmax_y) / 2.0
            dx = center_x - source_center_x
            dy = center_y - source_center_y

            if cell.path is not None:
                source_doc = read_dxf_document(cell.path)
                copy_resources(target, source_doc)
                for entity in source_doc.modelspace():
                    copy_visible_entity(msp, entity, dx, dy)
            else:
                add_label(
                    msp,
                    "程序未生成可供审核的拆板候选",
                    content_left + 300.0,
                    center_y,
                    height=150.0,
                    color=1,
                )

            placed = {
                "min_x": cell.extmin_x + dx,
                "min_y": cell.extmin_y + dy,
                "max_x": cell.extmax_x + dx,
                "max_y": cell.extmax_y + dy,
            }
            container = {
                "min_x": content_left,
                "min_y": content_bottom,
                "max_x": content_right,
                "max_y": content_top,
            }
            if not (
                placed["min_x"] >= container["min_x"] - 0.01
                and placed["min_y"] >= container["min_y"] - 0.01
                and placed["max_x"] <= container["max_x"] + 0.01
                and placed["max_y"] <= container["max_y"] + 0.01
            ):
                raise ValueError(f"图形超出单元框: {task.display_name} / {cell.role}")
            manifest_cells.append(
                {
                    "role": cell.role,
                    "path": str(cell.path) if cell.path is not None else None,
                    "frame": {"min_x": x_left, "min_y": cell_bottom, "max_x": cell_right, "max_y": cell_top},
                    "content_frame": container,
                    "placed_extent": placed,
                }
            )
            x_left = cell_right + CELL_GAP

        manifest_tasks.append(
            {
                "sequence": task.sequence,
                "display_name": task.display_name,
                "family": task.family,
                "route": task.route,
                "task_dir": task.task_dir,
                "row_frame": {"min_x": 0.0, "min_y": row_bottom, "max_x": row_width, "max_y": y_top},
                "cells": manifest_cells,
            }
        )
        y_top = row_bottom - ROW_GAP
        if task.sequence % 20 == 0 or task.sequence == len(tasks):
            print(f"{result_json.stem}: {task.sequence}/{len(tasks)}")

    output_dxf.parent.mkdir(parents=True, exist_ok=True)
    target.saveas(output_dxf)
    manifest = {
        "schema": "MERGED-REVIEW-SHEET-1.0",
        "result_json": str(result_json),
        "output_dxf": str(output_dxf),
        "counts": counts,
        "included_task_count": len(tasks),
        "row_count": len(tasks),
        "cell_count": sum(len(task.cells) for task in tasks),
        "tasks": manifest_tasks,
    }
    manifest_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    result_files = sorted(args.root.glob("*_result.json"), key=lambda p: natural_key(p.name))
    if len(result_files) != 3:
        raise ValueError(f"期望3份结果清单，实际{len(result_files)}份")
    process_dir = args.output / "99_生成过程"
    process_dir.mkdir(parents=True, exist_ok=True)
    combined_items: list[dict[str, object]] = []
    for result_file in result_files:
        batch_name = result_file.stem.removesuffix("_result")
        payload = read_json_payload(result_file)
        for item in payload:
            copied = dict(item)
            original_name = Path(
                str(copied.get("task_dir") or copied["input"])
            ).stem
            copied["task_dir"] = f"{batch_name}__{original_name}"
            combined_items.append(copied)

    combined_json = process_dir / "三批最新程序拆板结果_合并清单.json"
    combined_json.write_text(
        json.dumps(combined_items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_dxf = process_dir / "三批原图与最新程序拆板结果_总合并图.dxf"
    manifest_json = process_dir / "三批总合并图_配对与版式清单.json"
    merge_batch(combined_json, output_dxf, manifest_json)


if __name__ == "__main__":
    main()
