"""CLI entry point — typer commands for extract and validate."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from loguru import logger

app = typer.Typer(
    name="dxf2excel",
    help="从AutoCAD DXF文件中提取GGZ/MSZJ材料表，合并输出为Excel",
)


@app.command()
def extract(
    input_dir: Path = typer.Argument(
        default=...,
        help="包含.dxf文件的输入目录",
        exists=True,
        file_okay=False,
        dir_okay=True,
    ),
    output: Path = typer.Option(
        default=Path("output/material_tables.xlsx"),
        help="输出的.xlsx文件路径",
    ),
    verbose: bool = typer.Option(
        default=False,
        help="详细日志输出",
    ),
) -> None:
    """处理input_dir下所有.dxf文件，提取材料表并输出到Excel。"""
    if verbose:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")
    else:
        logger.remove()
        logger.add(sys.stderr, level="INFO")

    output.parent.mkdir(parents=True, exist_ok=True)

    from .pipeline import process_all

    process_all(input_dir, output)


@app.command()
def validate(
    dxf_file: Path = typer.Argument(
        default=...,
        help="单个.dxf文件路径",
        exists=True,
        file_okay=True,
    ),
) -> None:
    """验证单个DXF文件的解析结果，输出块统计和表格候选信息。"""
    from .candidate import detect_drawing_type, identify_table_blocks
    from .pipeline import process_file
    from .reader import read_dxf_blocks

    logger.remove()
    logger.add(sys.stderr, level="INFO")

    # Quick block scan
    blocks = read_dxf_blocks(dxf_file)
    candidates = identify_table_blocks(blocks)

    dt = detect_drawing_type(dxf_file.name)
    print(f"\nFile: {dxf_file.name}")
    print(f"Drawing type: {dt.value}")
    print(f"Anonymous blocks with entities: {len(blocks)}")
    print(f"Table candidates (score >= 0.3): {len(candidates)}")
    print()

    if not candidates:
        print("No table candidates found. Block stats:")
        from .candidate import compute_block_stats, score_candidate

        for bn, (texts, lines) in sorted(blocks.items()):
            if len(texts) + len(lines) >= 10:
                s = compute_block_stats(bn, texts, lines)
                sc = score_candidate(s)
                print(f"  {bn:8s}  TEXT={len(texts):4d}  LINE={len(lines):5d}  score={sc:.3f}")
        return

    # Full pipeline on top candidate
    print("Running full extraction pipeline...")
    results, warnings = process_file(dxf_file)

    if results:
        table = results[0]
        print(f"\nGrid: {table.num_rows} rows × {table.num_cols} cols")
        print(f"Block: {table.source_block}")
        print(f"BBox: ({table.bbox_x1:.0f},{table.bbox_y1:.0f}) → ({table.bbox_x2:.0f},{table.bbox_y2:.0f})")
        print(f"Candidate score: {table.candidate_score:.3f}")
        print(f"Grid score: {table.grid_score:.3f}")
        print(f"Fill rate: {table.fill_rate:.1%}")
        print(f"Data rows: {len(table.data_rows)}")
        print(f"Warnings: {len(warnings)}")

        # Show row classifications
        print("\nRow classifications:")
        for gr in table.grid_rows:
            preview = " | ".join(
                c.merged_text[:20] if c.merged_text else ""
                for c in gr.cells[:4]
            )
            print(f"  Row {gr.row_index:2d} [{gr.row_type.value:10s}]: {preview[:80]}")

        # Show first few data rows
        if table.data_rows:
            print("\nSample data rows:")
            for row in table.data_rows[:3]:
                print(f"  {row.part_no or '':20s} {str(row.spec or ''):20s} "
                      f"L={row.length_mm}  {row.material or '':8s} "
                      f"Q={row.quantity}  UW={row.unit_weight_kg}  TW={row.total_weight_kg}"
                      f"{'  comp=' + row.component_no if row.component_no else ''}")

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings[:10]:
            print(f"  [{w.warning_code}] {w.message}")


@app.callback()
def main() -> None:
    """dxf2excel — DXF材料表提取工具"""
    pass


if __name__ == "__main__":
    app()
