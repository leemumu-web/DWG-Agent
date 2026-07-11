"""multi_split — 钢结构型材/板材智能拆分工具

从 SunFire VBA 插件转译而来，纯 Python 实现，无需 Excel。

核心功能
========
- **型材拆分** (profile): H型钢/工字钢/板材规格自动拆分 → 腹板+翼缘
- **构件BOM** (bom): 零件清单 → 构件汇总 (qdmade)
- **合并** (combination): 等条件行合并求和
- **排序** (sort): 多条件排序 (multisort)
- **对照表** (crossref): 两表对照合并 (mddzb)
- **填充** (fill): 空白单元格向下填充 (fillin)
- **TXT导入** (txt_import): SELX导出TXT导入 (transtxt)

使用示例::

    from multi_split import split_profile_excel, split_profile_df

    # Excel 文件级别: 读取"整理表"，输出"整理表_拆板后"
    split_profile_excel("project.xlsx", sheet_name="整理表")

    # DataFrame 级别: 处理内存中的数据
    result = split_profile_df(df, spec_col="规格", width_col="宽度")

    # 构件BOM
    from multi_split.bom import qdmade
    bom = qdmade(df, other_cols=["图号"], unique_cols=["构件号"])
"""

# Core (always available, minimal imports)
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


# ---------------------------------------------------------------------------
# Lazy imports for optional / heavy sub-modules.
# Usage:  from multi_split import fillin    # triggers lazy load
#         from multi_split.bom import qdmade  # also works
# ---------------------------------------------------------------------------

def __getattr__(name: str):
    _LAZY = {
        "SortSpec": ".models",
        "ColumnMapping": ".models",
        "SunFireConfig": ".config",
        "read_excel": ".io",
        "write_excel": ".io",
        "fillin": ".fill",
        "multisort": ".sort",
        "multisort_from_strings": ".sort",
        "combination_check": ".combination",
        "combination_merge": ".combination",
        "combination_merge_legacy": ".combination",
        "mddzb": ".crossref",
        "transtxt": ".txt_import",
        "qdmade": ".bom",
    }
    if name in _LAZY:
        import importlib
        mod = importlib.import_module(_LAZY[name], __package__)
        attr = getattr(mod, name)
        # Cache in globals so __getattr__ is only called once per name
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
