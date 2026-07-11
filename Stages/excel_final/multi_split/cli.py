"""Command-line interface for multi_split.

Usage:
    multi-split project.xlsx -s 整理表
    multi-split project.xlsx -s 整理表 --spec 规格 --width 宽度 --qty 数量 --part-type 零件类型
    multi-split project.xlsx -s 整理表 --mode BH --mode I --mode PL

Requires ``click`` to be installed: ``pip install click``.
"""

from .profile import split_profile_excel, DEFAULT_MODES


def main(
    excel_path,
    sheet_name="整理表",
    spec_col="规格",
    width_col="宽度",
    qty_col="数量",
    part_type_col="零件类型",
    modes=None,
    output_sheet=None,
    dry_run=False,
):
    """multi-split — 钢结构型材/板材智能拆分工具

    读取 Excel 文件中的指定子表，对 H 型钢、工字钢、板材规格进行自动拆分，
    在原文件中新增 "{sheet}_拆板后" 子表，原表保持不变。
    """
    mode_list = list(modes) if modes else DEFAULT_MODES
    out_name = output_sheet or f"{sheet_name}_拆板后"

    if dry_run:
        print(f"📄 文件: {excel_path}")
        print(f"📋 处理子表: '{sheet_name}' → '{out_name}'")
        print(f"🔧 拆分类型: {', '.join(mode_list)}")
        print(f"📐 列映射: 规格={spec_col}, 宽度={width_col}, "
              f"数量={qty_col}, 零件类型={part_type_col}")
        print("🔍 仅预览模式，不修改文件")
        return

    result_name = split_profile_excel(
        excel_path=excel_path,
        sheet_name=sheet_name,
        spec_col=spec_col,
        width_col=width_col,
        qty_col=qty_col,
        part_type_col=part_type_col,
        modes=mode_list,
        output_sheet=out_name,
    )

    print(f"✅ 完成! 新子表 '{result_name}' 已写入 '{excel_path}'")
    print(f"   原表 '{sheet_name}' 保持不变")


def _click_main():
    """Click-based CLI entry point.  Requires ``click`` package."""
    import click

    @click.command()
    @click.argument("excel_path", type=click.Path(exists=True))
    @click.option(
        "-s", "--sheet", "sheet_name",
        default="整理表",
        help="要处理的子表名称 (default: 整理表)",
    )
    @click.option(
        "--spec", "spec_col",
        default="规格",
        help="规格所在列名 (default: 规格)",
    )
    @click.option(
        "--width", "width_col",
        default="宽度",
        help="宽度所在列名 (default: 宽度)",
    )
    @click.option(
        "--qty", "qty_col",
        default="数量",
        help="数量列名 (default: 数量)",
    )
    @click.option(
        "--part-type", "part_type_col",
        default="零件类型",
        help="零件类型列名 (default: 零件类型)",
    )
    @click.option(
        "--mode", "modes",
        multiple=True,
        type=click.Choice(["BH", "I", "PL"]),
        help="要拆分的型材类型 (可重复, default: BH I PL 三者全选)",
    )
    @click.option(
        "-o", "--output-sheet",
        default=None,
        help="输出子表名称 (default: {sheet_name}_拆板后)",
    )
    @click.option(
        "-n", "--dry-run",
        is_flag=True,
        help="仅打印处理结果，不修改文件",
    )
    def _cmd(excel_path, sheet_name, spec_col, width_col, qty_col,
             part_type_col, modes, output_sheet, dry_run):
        """multi-split — 钢结构型材/板材智能拆分工具"""
        mode_list = list(modes) if modes else DEFAULT_MODES
        out_name = output_sheet or f"{sheet_name}_拆板后"

        click.echo(f"📄 文件: {excel_path}")
        click.echo(f"📋 处理子表: '{sheet_name}' → '{out_name}'")
        click.echo(f"🔧 拆分类型: {', '.join(mode_list)}")
        click.echo(f"📐 列映射: 规格={spec_col}, 宽度={width_col}, "
                   f"数量={qty_col}, 零件类型={part_type_col}")

        if dry_run:
            click.echo("🔍 仅预览模式，不修改文件")
            return

        result_name = split_profile_excel(
            excel_path=excel_path,
            sheet_name=sheet_name,
            spec_col=spec_col,
            width_col=width_col,
            qty_col=qty_col,
            part_type_col=part_type_col,
            modes=mode_list,
            output_sheet=out_name,
        )

        click.echo(f"✅ 完成! 新子表 '{result_name}' 已写入 '{excel_path}'")
        click.echo(f"   原表 '{sheet_name}' 保持不变")

    _cmd()


if __name__ == "__main__":
    _click_main()
