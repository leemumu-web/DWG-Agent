"""Excel 第三阶段核心逻辑 — 异孔折判断对接与回填。"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter

from yikongzhe.classifier import classify_directory

logger = logging.getLogger(__name__)

# 待人工单元格红色背景
_RED_FILL = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")

# DXF 文件名中需要剥离的后缀
_DXF_STRIP_SUFFIXES = [
    "_拆板后", "_拆分后", "_拆板", "_拆分",
    "_处理后", "_处理",
]

# 零件名中标识 BH/BOX 类型的前缀模式（捕获完整后缀）
_PART_TYPE_RE = re.compile(r"-(BH|BOX)(.+)")

# 分类结果 part_name 中的前缀
_CLASSIFICATION_NAME_PREFIX = "p="


def _normalize_part_type(suffix: str) -> str:
    """将零件名后缀归一化为"腹"或"翼"。

    "上翼" / "下翼" / "翼" → "翼"
    "腹" / "腹板1"        → "腹"
    """
    if "翼" in suffix:
        return "翼"
    if "腹" in suffix:
        return "腹"
    return suffix


def _extract_base_part_number(part_name: str) -> str | None:
    """从零件名中提取基础板号。

    "3b1-cb-133-BH腹"  → "3b1-cb-133"
    "3b1-cb-4-BH上翼"  → "3b1-cb-4"
    "3b1-s-19"         → None（非 BH/BOX 零件）
    """
    m = _PART_TYPE_RE.search(part_name)
    if not m:
        return None
    return part_name[:m.start()]


def _extract_part_category(part_name: str) -> str | None:
    """从零件名中提取归一化的板件部位类别。

    "3b1-cb-133-BH腹"  → "腹"
    "3b1-cb-4-BH上翼"  → "翼"
    "3b1-cb-4-BH腹板1" → "腹"
    "3b1-s-19"         → None
    """
    m = _PART_TYPE_RE.search(part_name)
    if not m:
        return None
    return _normalize_part_type(m.group(2))


def _strip_dxf_suffix(filename: str) -> str:
    """去除 DXF 文件名中的通用后缀，返回干净的 stem。

    "b4-1-cb-15_拆板后.dxf" → "b4-1-cb-15"
    "b4-1-cb-15.dxf"        → "b4-1-cb-15"
    """
    stem = Path(filename).stem
    for suffix in _DXF_STRIP_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return stem


def _build_dxf_index(dxf_dir: str) -> dict[str, str]:
    """构建 DXF 文件名索引。

    遍历目录下所有 .dxf/.dwg 文件，建立
    {干净stem → 完整文件名} 的映射。

    Returns:
        {"b4-1-cb-15": "b4-1-cb-15_拆板后.dxf", ...}
    """
    index: dict[str, str] = {}
    base = Path(dxf_dir)
    if not base.is_dir():
        return index

    for f in base.iterdir():
        if f.is_file() and f.suffix.lower() in (".dxf", ".dwg"):
            clean = _strip_dxf_suffix(f.name)
            index[clean] = f.name
    return index


def _classify_part_name_to_type(name: str) -> str | None:
    """从分类结果的 part_name 中提取归一化的板件部位类别。

    "p=b4-1-cb-15腹" → "腹"
    "p=b4-1-cb-15翼" → "翼"
    """
    clean = name
    if clean.startswith(_CLASSIFICATION_NAME_PREFIX):
        clean = clean[len(_CLASSIFICATION_NAME_PREFIX):]
    if "翼" in clean:
        return "翼"
    if "腹" in clean:
        return "腹"
    return None


class Stage3Runner:
    """第三阶段处理运行器。

    1. 读取第二阶段 Excel 的 part 表
    2. 提取 BH/BOX 零件，匹配拆板后 DXF
    3. 调用 yikongzhe 分类
    4. 回填"图形"列，标红"待人工"
    5. 输出两个 Excel 文件
    """

    def __init__(
        self,
        stage2_excel_path: str,
        dxf_dir: str,
        output_dir: str,
        encoding: str = "utf-8",
    ):
        self.stage2_excel_path = stage2_excel_path
        self.dxf_dir = dxf_dir
        self.output_dir = output_dir
        self.encoding = encoding
        # 列索引（1-based），在 _read_part_rows 中检测
        self._col_part_name: int | None = None
        self._col_component: int | None = None
        self._col_graphics: int | None = None

    def run(self) -> dict[str, object]:
        """执行完整流水线，返回统计信息。"""
        os.makedirs(self.output_dir, exist_ok=True)

        # 1. 读取 part 表
        logger.info("读取 Stage2 Excel: %s", self.stage2_excel_path)
        wb = openpyxl.load_workbook(self.stage2_excel_path)
        if "part" not in wb.sheetnames:
            raise ValueError(f"Excel 文件中未找到 'part' 工作表，现有表: {wb.sheetnames}")
        ws = wb["part"]

        part_rows = self._read_part_rows(ws)
        logger.info("part 表共 %d 行数据", len(part_rows))

        # 2. 构建 DXF 索引
        dxf_index = _build_dxf_index(self.dxf_dir)
        logger.info("DXF 目录中共 %d 个文件", len(dxf_index))

        # 3. 匹配 part → DXF
        part_dxf_map, unmatched = self._match_parts_to_dxfs(part_rows, dxf_index)
        bh_box_count = sum(
            1 for r in part_rows
            if _extract_base_part_number(str(r.get("_part_name", "") or ""))
        )
        logger.info(
            "BH/BOX 零件共 %d 行, 匹配到 DXF: %d 行, 未匹配: %d 行",
            bh_box_count, len(part_dxf_map), len(unmatched),
        )

        # 4. 收集需要分类的唯一 DXF
        unique_dxfs = set(part_dxf_map.values())
        logger.info("待分类的唯一 DXF: %d 个", len(unique_dxfs))

        # 5. 运行分类
        classification_map = self._run_classification(unique_dxfs)
        logger.info("分类完成: %d 个 DXF", len(classification_map))

        # 6. 回填"图形"列
        filled, manual = self._fill_graphics_column(ws, part_rows, part_dxf_map, classification_map)

        # 7. 输出文件
        output_paths = self._write_outputs(wb, part_rows, part_dxf_map, classification_map)

        wb.close()
        logger.info("所有输出已保存到: %s", self.output_dir)

        return {
            "classification_excel": output_paths.get("classification", ""),
            "deepened_excel": output_paths.get("deepened", ""),
            "bh_box_count": bh_box_count,
            "matched_count": len(part_dxf_map),
            "unmatched_count": len(unmatched),
            "classified_dxf_count": len(classification_map),
            "filled_count": filled,
            "manual_count": manual,
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _find_col(headers: list, keyword: str) -> int | None:
        """在表头中按关键字查找列索引（1-based）。"""
        for i, h in enumerate(headers):
            if h and keyword in str(h):
                return i + 1
        return None

    def _read_part_rows(self, ws) -> list[dict[str, Any]]:
        """读取 part 表数据行，同时检测关键列索引。

        Returns:
            [{"_row": 2, "_part_name": "...", "_component": "...", ...}, ...]
        """
        headers = []
        for c in range(1, ws.max_column + 1):
            headers.append(ws.cell(1, c).value)

        # 按关键字检测列
        self._col_part_name = self._find_col(headers, "零件")
        self._col_component = self._find_col(headers, "构件")
        self._col_graphics = self._find_col(headers, "图形")

        logger.debug(
            "列检测: 零件名=col%d, 构件编号=col%d, 图形=col%d",
            self._col_part_name or -1,
            self._col_component or -1,
            self._col_graphics or -1,
        )

        rows = []
        for r in range(2, ws.max_row + 1):
            row_data: dict[str, Any] = {"_row": r}
            # 读取关键列
            if self._col_part_name:
                row_data["_part_name"] = ws.cell(r, self._col_part_name).value
            if self._col_component:
                row_data["_component"] = ws.cell(r, self._col_component).value
            # 跳过完全空行
            if any(v is not None for k, v in row_data.items() if k != "_row"):
                rows.append(row_data)
        return rows

    def _match_parts_to_dxfs(
        self, part_rows: list[dict], dxf_index: dict[str, str]
    ) -> tuple[dict[int, str], list[int]]:
        """建立 part 行 → DXF 文件名 的映射。

        Returns:
            ({row_num → dxf_filename}, [unmatched_row_nums])
        """
        part_dxf_map: dict[int, str] = {}
        unmatched: list[int] = []

        for row in part_rows:
            part_name = str(row.get("_part_name", "") or "")
            base = _extract_base_part_number(part_name)
            if base is None:
                continue  # 非 BH/BOX 零件

            if base in dxf_index:
                part_dxf_map[row["_row"]] = dxf_index[base]
            else:
                # 尝试不区分大小写匹配
                base_lower = base.lower()
                found = None
                for key, val in dxf_index.items():
                    if key.lower() == base_lower:
                        found = val
                        break
                if found:
                    part_dxf_map[row["_row"]] = found
                else:
                    unmatched.append(row["_row"])
                    logger.debug("未匹配到 DXF: %s (base=%s)", part_name, base)

        return part_dxf_map, unmatched

    def _run_classification(
        self, dxf_filenames: set[str]
    ) -> dict[str, dict[str, str]]:
        """对指定 DXF 文件所在目录运行分类。

        classify_directory 会处理整个目录，我们只提取需要的 DXF 结果。

        Returns:
            {dxf_filename: {"腹": "方孔折", "翼": "方", ...}}
        """
        if not dxf_filenames:
            return {}

        all_results = classify_directory(self.dxf_dir, encoding=self.encoding)
        classification_map: dict[str, dict[str, str]] = {}

        for result in all_results:
            if result.dxf_file not in dxf_filenames:
                continue
            part_map: dict[str, str] = {}
            for pc in result.parts:
                ptype = _classify_part_name_to_type(pc.part_name)
                if ptype:
                    part_map[ptype] = pc.category
                else:
                    # 无法识别类型 → 使用 part_name 原样作为 key
                    clean = pc.part_name
                    if clean.startswith(_CLASSIFICATION_NAME_PREFIX):
                        clean = clean[len(_CLASSIFICATION_NAME_PREFIX):]
                    part_map[clean] = pc.category
            classification_map[result.dxf_file] = part_map

        return classification_map

    def _fill_graphics_column(
        self,
        ws,
        part_rows: list[dict],
        part_dxf_map: dict[int, str],
        classification_map: dict[str, dict[str, str]],
    ) -> tuple[int, int]:
        """回填"图形"列并标红"待人工"。

        对于在分类映射中找不到的零件（如无匹配 DXF），跳过不填。

        Returns:
            (filled_count, manual_count)
        """
        if self._col_graphics is None:
            logger.warning("未检测到'图形'列，跳过回填")
            return 0, 0

        col_letter = get_column_letter(self._col_graphics)
        filled = 0
        manual = 0

        for row in part_rows:
            row_num = row["_row"]
            if row_num not in part_dxf_map:
                continue

            dxf_filename = part_dxf_map[row_num]
            if dxf_filename not in classification_map:
                continue

            part_maps = classification_map[dxf_filename]
            part_name = str(row.get("_part_name", "") or "")
            ptype = _extract_part_category(part_name)

            # 确定分类结果
            category: str | None = None
            if ptype and ptype in part_maps:
                category = part_maps[ptype]
            elif part_maps:
                # 类型无法识别：如果 DXF 只有一个分类结果，直接使用
                if len(part_maps) == 1:
                    category = next(iter(part_maps.values()))
                else:
                    for key, val in part_maps.items():
                        if key and key in part_name:
                            category = val
                            break

            if category is None:
                logger.debug("无法确定分类: row=%d, part=%s", row_num, part_name)
                continue

            cell = ws.cell(row_num, self._col_graphics)
            cell.value = category
            filled += 1

            if category == "待人工":
                cell.fill = _RED_FILL
                manual += 1

        logger.info("回填完成: %d 行, 其中待人工 %d 行", filled, manual)
        return filled, manual

    def _write_outputs(
        self,
        wb,
        part_rows: list[dict],
        part_dxf_map: dict[int, str],
        classification_map: dict[str, dict[str, str]],
    ) -> dict[str, str]:
        """输出两个 Excel 文件，返回路径映射。"""
        classification_path = self._write_classification_excel(
            part_rows, part_dxf_map, classification_map,
        )

        deepened_path = os.path.join(self.output_dir, "stage2_with_graphics.xlsx")
        wb.save(deepened_path)
        logger.info("深化后 Excel 已保存: %s", deepened_path)

        return {"classification": classification_path, "deepened": deepened_path}

    def _write_classification_excel(
        self,
        part_rows: list[dict],
        part_dxf_map: dict[int, str],
        classification_map: dict[str, dict[str, str]],
    ) -> str:
        """生成分类结果 Excel，返回输出路径。"""
        wb_out = openpyxl.Workbook()
        ws_out = wb_out.active
        ws_out.title = "分类结果"

        out_headers = ["零件名称", "构件编号", "基础板号", "部位", "图形类别", "来源DXF"]
        for c, h in enumerate(out_headers, 1):
            ws_out.cell(1, c, h)

        row_out = 2
        for row in part_rows:
            row_num = row["_row"]
            part_name = str(row.get("_part_name", "") or "")
            component = str(row.get("_component", "") or "")
            base = _extract_base_part_number(part_name)
            ptype = _extract_part_category(part_name)

            if row_num not in part_dxf_map:
                continue

            dxf_filename = part_dxf_map[row_num]
            if dxf_filename not in classification_map:
                continue

            part_maps = classification_map[dxf_filename]
            category: str | None = None
            if ptype and ptype in part_maps:
                category = part_maps[ptype]
            elif len(part_maps) == 1:
                category = next(iter(part_maps.values()))
            else:
                for key, val in part_maps.items():
                    if key and key in part_name:
                        category = val
                        break

            if category is None:
                continue

            ws_out.cell(row_out, 1, part_name)
            ws_out.cell(row_out, 2, component)
            ws_out.cell(row_out, 3, base or "")
            ws_out.cell(row_out, 4, ptype or "")
            ws_out.cell(row_out, 5, category)
            ws_out.cell(row_out, 6, dxf_filename)

            if category == "待人工":
                for c in range(1, len(out_headers) + 1):
                    ws_out.cell(row_out, c).fill = _RED_FILL

            row_out += 1

        output_path = os.path.join(self.output_dir, "分类结果.xlsx")
        wb_out.save(output_path)
        logger.info("分类结果 Excel 已保存: %s", output_path)
        return output_path
