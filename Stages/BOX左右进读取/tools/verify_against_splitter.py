"""全量对照：BOX 左右进读取器 vs 拆板（split_output report.json）。

对比方法（总进 = 名义长度 − 板长，不区分左右方向——镜像图左右互换不影响总进）：
- 读取器腹板角色（腹×2 / 上腹+下腹）总进集合 vs 拆板 web_left/web_right 总进集合
  （无序匹配，容差 3mm；拆板两块相同 → 读取器应合并"腹"，两块不同 → 应拆分）
- 读取器翼板角色（翼×2 / 上翼+下翼）总进集合 vs 拆板 flange_top/flange_bottom

用法：
  uv run python tools/verify_against_splitter.py [--reader box-measurements.json]
      [--split-root /path/to/split_output_pr6/auto_accepted/box]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TOLERANCE_MM = 3.0


def load_splitter_data(split_root: Path) -> dict[str, dict[str, float]]:
    """part_number -> {role: 总进 mm}"""
    result: dict[str, dict[str, float]] = {}
    for report in sorted(split_root.glob("*/*_report.json")):
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        mir = payload.get("manufacturing_ir") or {}
        nominal = float(mir.get("nominal_length_mm") or payload.get("metadata", {}).get("nominal_length_mm") or 0)
        data: dict[str, float] = {}
        for plate in mir.get("physical_plates", []):
            role = str(plate.get("role") or "")
            if role not in ("web_left", "web_right", "flange_top", "flange_bottom"):
                continue
            contract = plate.get("weld_allowance_contract") or {}
            length = contract.get("main_length_mm")
            if length is None:
                continue
            data[role] = float(length)
        if not data or not nominal:
            continue
        part = report.parent.name.split("@")[-1].replace("_拆板前", "")
        result[part] = {
            role: max(0.0, nominal - length)
            for role, length in data.items()
        }
    return result


def reader_setbacks(item: dict) -> dict[str, dict[str, float]]:
    """读取器角色 -> {left, right, total}"""
    out: dict[str, dict[str, float]] = {}
    for measurement in item.get("measurements", []):
        role = measurement["role"]
        left = float(measurement["left_raw"])
        right = float(measurement["right_raw"])
        out[role] = {"left": left, "right": right, "total": left + right}
    return out


def match_totals(reader_totals: list[float], splitter_totals: list[float]) -> bool:
    """匹配读取器板件与拆板板件。

    拆板总是输出 web_left+web_right（及 flange_top+bottom）两条；两条近似相同
    （拆板成对等价）时读取器合并为一条"腹/翼"（×2），此时 1 条对 2 条仍算匹配。
    """
    remaining = list(splitter_totals)
    for value in reader_totals:
        best = min(remaining, key=lambda other: abs(other - value), default=None)
        if best is None or abs(best - value) > TOLERANCE_MM:
            return False
        remaining.remove(best)
    # 读取器已匹配全部条数；拆板剩余条目必须与已匹配值近似相同（合并语义）
    return all(
        any(abs(left - matched) <= TOLERANCE_MM for matched in reader_totals)
        for left in remaining
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reader",
        default="outputs/box-measurements.json",
    )
    parser.add_argument(
        "--split-root",
        default=(
            "/home/Creeken/Paper/CAD_research/complete_framework/"
            "BOX拆板前分类/split_output_pr6/auto_accepted/box"
        ),
    )
    args = parser.parse_args()

    reader_items = {
        item["part_number"]: item
        for item in json.loads(Path(args.reader).read_text(encoding="utf-8"))["items"]
    }
    splitter = load_splitter_data(Path(args.split_root))
    print(f"读取器 {len(reader_items)} 张 / 拆板 {len(splitter)} 张")

    web_ok = flange_ok = 0
    web_mismatch: list[tuple[str, str, list[float], list[float]]] = []
    flange_mismatch: list[tuple[str, str, list[float], list[float]]] = []
    not_found = 0
    for part, item in reader_items.items():
        if part not in splitter:
            not_found += 1
            continue
        if item.get("status") != "OK":
            # 读取器失败状态（如 ERROR_CRANKED_UNSUPPORTED 折线构件 -> Excel
            # 标红人工处理）不进"不匹配"统计。
            continue
        setbacks = reader_setbacks(item)
        sp = splitter[part]
        # 腹板对比：读取器 {腹} 或 {上腹,下腹} vs 拆板 {web_left, web_right}
        reader_webs: list[float] = []
        reader_web_roles: list[str] = []
        for role in ("腹", "上腹", "下腹"):
            if role in setbacks:
                reader_webs.append(setbacks[role]["total"])
                reader_web_roles.append(role)
        splitter_webs = [sp["web_left"], sp["web_right"]]
        if match_totals(reader_webs, splitter_webs):
            web_ok += 1
        else:
            web_mismatch.append((part, "/".join(reader_web_roles), reader_webs, splitter_webs))
        # 翼板对比
        reader_flanges: list[float] = []
        reader_flange_roles: list[str] = []
        for role in ("翼", "上翼", "下翼"):
            if role in setbacks:
                reader_flanges.append(setbacks[role]["total"])
                reader_flange_roles.append(role)
        splitter_flanges = [sp["flange_top"], sp["flange_bottom"]]
        if match_totals(reader_flanges, splitter_flanges):
            flange_ok += 1
        else:
            flange_mismatch.append((part, "/".join(reader_flange_roles), reader_flanges, splitter_flanges))

    compared = len(reader_items) - not_found
    print(f"对照 {compared} 张：腹板对齐 {web_ok}，翼板对齐 {flange_ok}，拆板无输出 {not_found}")
    if web_mismatch:
        print(f"\n=== 腹板不匹配 {len(web_mismatch)} 张 ===")
        for part, roles, rv, sv in sorted(web_mismatch):
            print(f"  {part} [{roles}] 读取器总进 {[round(v,1) for v in rv]} vs 拆板 {[round(v,1) for v in sv]}")
    if flange_mismatch:
        print(f"\n=== 翼板不匹配 {len(flange_mismatch)} 张 ===")
        for part, roles, rv, sv in sorted(flange_mismatch):
            print(f"  {part} [{roles}] 读取器总进 {[round(v,1) for v in rv]} vs 拆板 {[round(v,1) for v in sv]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
