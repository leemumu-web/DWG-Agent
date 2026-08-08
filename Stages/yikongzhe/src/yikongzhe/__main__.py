"""CLI 入口。

将分类结果输出到 Excel。
支持 DWG 输入：自动转为 DXF 后再分类（不修改原文件）。

用法:
    uv run python -m yikongzhe <输入目录> [--output 输出.xlsx] [--encoding utf-8]
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from yikongzhe.classifier import classify_directory
from yikongzhe.excel_writer import write_excel

logger = logging.getLogger(__name__)


def _find_oda_exe() -> str | None:
    """探测 ODA File Converter 可执行文件路径（Windows/跨平台）。

    搜索顺序:
    1. 环境变量 ODA_EXE / ODAFILE_CONVERTER
    2. PATH 中的 ODAFileConverter.exe / ODAFileConverter
    3. 当前项目 tools/oda/ 目录
    4. ezdxf.odafc 内置探测
    """
    # 环境变量
    for var in ("ODA_EXE", "ODAFILE_CONVERTER"):
        val = os.environ.get(var)
        if val and Path(val).exists():
            return val

    # PATH 搜索
    for name in ("ODAFileConverter.exe", "ODAFileConverter"):
        found = shutil.which(name)
        if found:
            return found

    # 项目 tools/oda/ 目录
    repo_root = Path(__file__).resolve().parents[3]  # Stages/yikongzhe/src/yikongzhe/ -> repo root
    tools_oda = repo_root / "tools" / "oda"
    if tools_oda.exists():
        for f in tools_oda.iterdir():
            if f.name.startswith("ODAFileConverter") and f.is_file():
                return str(f)

    # ezdxf.odafc 探测
    try:
        from ezdxf.odafc import find_oda
        oda_path = find_oda()
        if oda_path:
            return str(oda_path)
    except Exception:
        pass

    return None


def _convert_dwg_directory(
    input_dir: Path, temp_dir: Path
) -> int:
    """将目录下所有 .dwg 转为 .dxf 存入 temp_dir。

    不修改原目录中的任何文件。

    Args:
        input_dir: 原始输入目录。
        temp_dir: 临时输出目录（.dxf 产物放这里）。

    Returns:
        成功转换的文件数。
    """
    dwg_files = list(input_dir.glob("*.dwg")) + list(input_dir.glob("*.DWG"))
    if not dwg_files:
        return 0

    oda_exe = _find_oda_exe()
    if oda_exe is None:
        logger.warning(
            "检测到 %d 个 DWG 文件，但未找到 ODA File Converter，跳过转换。"
            "请安装 ODA File Converter 或将 ODA_EXE 环境变量指向其路径。",
            len(dwg_files),
        )
        return 0

    logger.info("使用 ODA: %s", oda_exe)
    logger.info("转换 %d 个 DWG → DXF...", len(dwg_files))

    # ODA 第一个参数是目录，不是单个文件，一次调用批量转换
    try:
        subprocess.run(
            [oda_exe, str(input_dir), str(temp_dir), "ACAD2018", "DXF", "0", "1", "*.dwg"],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        logger.warning("DWG 转换超时")
    except Exception as exc:
        logger.warning("DWG 转换异常: %s", exc)

    # 检查产物
    converted = 0
    for dwg in dwg_files:
        expected_dxf = temp_dir / f"{dwg.stem}.dxf"
        if expected_dxf.exists():
            converted += 1
            logger.debug("  %s → %s", dwg.name, expected_dxf.name)
        else:
            logger.warning("  %s 转换失败（无 DXF 产物）", dwg.name)

    logger.info("DWG 转换完成: %d/%d 成功", converted, len(dwg_files))
    return converted


def _prepare_input_dir(input_dir: str) -> tuple[str, str | None]:
    """准备分类输入目录。

    如果输入目录包含 .dwg 文件，先转为 .dxf 到临时目录，
    不修改原文件。

    Args:
        input_dir: 用户指定的输入目录。

    Returns:
        (实际用于分类的目录, 临时目录路径或None)。
        调用方应在分类完成后清理临时目录。
    """
    base = Path(input_dir)
    if not base.is_dir():
        return input_dir, None

    dwg_files = list(base.glob("*.dwg")) + list(base.glob("*.DWG"))
    dxf_files = list(base.glob("*.dxf")) + list(base.glob("*.DXF"))

    # 如果只有 DXF 没有 DWG，直接返回原目录
    if not dwg_files:
        return input_dir, None

    logger.info("检测到 %d 个 DWG 文件，需要先转换", len(dwg_files))

    # 创建临时目录
    temp_dir = Path(tempfile.mkdtemp(prefix="yikongzhe_dwg_"))

    # 先复制已有 DXF 到临时目录
    for dxf in dxf_files:
        shutil.copy2(dxf, temp_dir / dxf.name)
    if dxf_files:
        logger.info("复制 %d 个现有 DXF 到临时目录", len(dxf_files))

    # 转换 DWG
    converted = _convert_dwg_directory(base, temp_dir)

    if converted == 0 and not dxf_files:
        logger.warning("DWG 转换全部失败且无现有 DXF，无法继续")
        # 清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)
        return input_dir, None

    return str(temp_dir), str(temp_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yikongzhe",
        description="异孔折判断 — DXF板件图形分类工具",
    )
    parser.add_argument(
        "input_dir",
        help="包含 DXF 文件的输入目录（会递归查找子目录中的 .dxf）",
    )
    parser.add_argument(
        "--output", "-o",
        default="分类结果.xlsx",
        help="输出 Excel 文件路径（默认: 分类结果.xlsx）",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="DXF 文件编码（默认: utf-8）",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="打印详细处理日志",
    )
    args = parser.parse_args()

    # 配置日志
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    # 预处理：如有 DWG 先转为 DXF（不影响原文件）
    actual_input_dir, temp_dir = _prepare_input_dir(args.input_dir)

    logger.info("开始处理目录: %s", actual_input_dir)

    try:
        results = classify_directory(actual_input_dir, encoding=args.encoding)
    except Exception as e:
        logger.error("分类失败: %s", e)
        sys.exit(1)
    finally:
        # 清理临时目录
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.debug("已清理临时目录: %s", temp_dir)

    if not results:
        logger.warning("未在目录中找到有效的 DXF 文件")
        sys.exit(0)

    total_parts = sum(len(r.parts) for r in results)
    logger.info("处理完成: %d 个 DXF, %d 块板", len(results), total_parts)

    try:
        write_excel(results, args.output)
    except Exception as e:
        logger.error("Excel 输出失败: %s", e)
        sys.exit(1)

    logger.info("结果已保存到: %s", args.output)


if __name__ == "__main__":
    main()