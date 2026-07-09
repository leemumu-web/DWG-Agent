# multi_split — 钢结构型材/板材智能拆分工具

从 SunFire VBA 插件转译，纯 Python 实现，**无需 Excel 即可运行**。

## 功能

读取 Excel 文件中指定子表，自动检测 **H 型钢**、**工字钢**、**板材** 规格并拆分为独立组件行。在原文件中新增 `{子表名}_拆板后` 子表，**原表完全不动**。

### 拆分逻辑

| 类型 | 检测前缀 | 拆分方式 | 示例 |
|------|---------|---------|------|
| H 型钢 | BH, HA | 腹板 + 翼缘×2 | BH300\*200\*6\*8 → t=6 H=284 + t=8 B=200×2 |
| 工字钢 | I, HI | 腹板 + 翼缘 | I250\*150\*5\*7 → t=5 H=236 + t=7 B=150 |
| 板材 | PL, - | 厚度×宽度排序 | PL2000\*10 → 10\*2000 |

## 对外四个核心接口

对应 Excel 表格的四列参数：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `spec_col` | 规格所在列 | `"规格"` |
| `width_col` | 宽度所在列 | `"宽度"` |
| `qty_col` | 数量列 | `"数量"` |
| `part_type_col` | 零件类型列 | `"零件类型"` |

默认**同时拆分 H 型钢、工字钢、板材**三种。

## 快速开始

### Python API

```python
from multi_split import split_profile_excel, split_profile_df

# 方式一: Excel 文件级别 (推荐)
# 读取"整理表"，在原文件中新增"整理表_拆板后"子表
split_profile_excel("project.xlsx", sheet_name="整理表")

# 自定义列名和输出表名
split_profile_excel(
    "project.xlsx",
    sheet_name="整理表",
    spec_col="规格",
    width_col="宽度",
    qty_col="数量",
    part_type_col="零件类型",
    modes=["BH", "I", "PL"],           # 三种全选 (默认)
    output_sheet="整理表_拆分结果",
)

# 只拆分 H 型钢和板材
split_profile_excel("project.xlsx", modes=["BH", "PL"])

# 方式二: DataFrame 级别
import pandas as pd
df = pd.read_excel("project.xlsx", sheet_name="整理表")
result = split_profile_df(df, spec_col="规格", width_col="宽度")
```

### 命令行

```bash
# 基本用法 (使用默认列名)
multi-split project.xlsx -s 整理表

# 自定义列名
multi-split project.xlsx -s 整理表 \
    --spec 规格 --width 宽度 --qty 数量 --part-type 零件类型

# 只拆分指定类型
multi-split project.xlsx -s 整理表 --mode BH --mode PL

# 指定输出子表名称
multi-split project.xlsx -s 整理表 -o 整理表_拆分后

# 仅预览，不修改文件
multi-split project.xlsx -s 整理表 -n
```

## 处理流程

```
输入 Excel                         输出 Excel
├── 整理表 (原样不动)          →   ├── 整理表 (原样不动)
│   ├── BH300*200*6*8              └── 整理表_拆板后 (新增)
│   ├── I250*150*5*7                   ├── 6 (H腹板)
│   ├── PL10*2000                      ├── 8 (H翼缘) ×2
│   └── L50*5                          ├── 5 (工腹板)
                                       ├── 7 (工翼缘)
                                       ├── 10, 2000 (板材)
                                       └── L50*5 (不拆分)
```

## 依赖

| 包 | 用途 |
|---|------|
| pandas >= 2.0 | 核心数据处理 |
| openpyxl >= 3.0 | Excel 文件读写 |
| click >= 8.0 | 命令行接口 |

## 背景

原始 SunFire 是 2005-2007 年由 wgch(国春) 开发的 Excel VBA 插件。本仓库将其型钢/板材拆分核心功能完整转译为 Python，保留原始作者署名。
