"""multi_split — 钢结构型材/板材智能拆分工具

从 SunFire VBA 插件转译而来，纯 Python 实现，无需 Excel。

对外暴露四个核心接口（对应 Excel 表格列）：
  1. spec_col   — 规格所在列
  2. width_col  — 宽度所在列
  3. qty_col    — 数量列
  4. part_type_col — 零件类型列

默认拆分 H 型钢、工字钢、板材三种类型，三者全选。

使用示例:
    from multi_split import split_profile_excel, split_profile_df

    # Excel 文件级别: 读取"整理表"，输出"整理表_拆板后"
    split_profile_excel("project.xlsx", sheet_name="整理表")

    # DataFrame 级别: 处理内存中的数据
    result = split_profile_df(df, spec_col="规格", width_col="宽度")
"""

from .profile import (
    split_profile_df,
    split_profile_excel,
    DEFAULT_MODES,
)

__all__ = [
    "split_profile_df",
    "split_profile_excel",
    "DEFAULT_MODES",
]
