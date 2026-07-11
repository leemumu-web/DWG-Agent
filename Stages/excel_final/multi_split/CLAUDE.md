# multi_split — 钢结构型材/板材智能拆分引擎

> **Integration boundary / 集成边界：** `multi_split` is a deterministic internal engine used by the standalone Excel Final Stage. It does not own FastAPI, Celery, MySQL Job state, storage permissions, or public error handling. 它不是独立平台服务；平台集成说明见 [`../README.md`](../README.md)。

从 SunFire VBA 插件 (`FRMSPLIT` + `frmQD` + `SortCriteria` + `FrmCombination` + `frmDZB` + `模块宏`) 完整转译，纯 Python，无需 Excel。

---

## 核心入口

### 1. Excel 文件级拆分

```python
from multi_split import split_profile_excel

sheet_name = split_profile_excel(
    "project.xlsx",            # str: .xlsx 路径
    sheet_name="整理表",        # str: 源子表
    spec_col="规格",            # str | int: 规格列名或 0-based 索引
    width_col="宽度",           # str | int: 宽度列
    qty_col="数量",             # str | int: 数量列
    part_type_col="零件类型",    # str | int: 零件类型列
    modes=None,                # list | None: None=['BH','I','PL']
    output_sheet=None,         # str | None: 输出子表名, None="{sheet}_拆板后"
)
# → str: 新建子表名
```

**行为**: 读 `sheet_name` → 自动检测表头行 (上半部分 ≥87.5% 非空) → 拆分 → 在同文件追加 `{sheet}_拆板后`。**源子表不动**。目标子表已存在则覆盖。

### 2. DataFrame 级拆分 (链式/内存)

```python
from multi_split import split_profile_df

result = split_profile_df(
    df,                        # pd.DataFrame
    spec_col="规格",
    width_col="宽度",
    qty_col="数量",
    part_type_col="零件类型",
    modes=None,                # None = ['BH', 'I', 'PL']
)
# → pd.DataFrame
```

无文件 I/O，纯数据变换。列名支持精确匹配、子串匹配、0-based 索引。

---

## 支持型材 & 拆分规则

| 前缀 | 类型 | 标签 (web/flange) | 腹板 qty | 翼缘 qty | 备注 |
|------|------|-------------------|:--------:|:--------:|------|
| `BH`, `HA` | H 型钢 | `BH腹` / `BH翼` | N | N×2 | VBA 原始 |
| `I`, `HI` | 工字钢 | `I腹` / `I翼` | N | N×2 | Python 扩展 |
| `BT` | T 型钢 | `BT腹` / `BT翼` | N | N | VBA 原始 |
| `BOX` | 箱型截面 | `BOX腹` / `BOX翼` | N×2 | N×2 | Python 扩展 |
| `PL`, `-` | 板材 | (不变) | N | — | 仅排序厚度×宽度 |

**标签全为 VBA 原始命名** (`BH腹/BH翼/BT腹/BT翼`)，`I`/`BOX` 按 VBA 命名惯例扩展。

### 不被拆分的类型

`HN`, `HW`, `HM`, `L`, `C`, `方管`, `D`, `M`, `NUT`, `TT` 等前缀不在识别集合中 → 原样保留。

### 默认模式

`DEFAULT_MODES = ["BH", "I", "PL"]` — BT 和 BOX 需显式传 `modes=["BH","I","PL","BT","BOX"]`。

---

## 输出格式

### 新增列

| 列 | 出现条件 | 值 |
|----|---------|-----|
| `拆分标记` | 至少有一行被拆分 | 被拆分行 = `"拆"`，其余 = `""`；全未拆分则无此列 |

### 行数变化

- BH/I/BT/BOX: 1→2 (腹板 + 翼缘)
- PL: 1→1 (规格/宽度重新赋值)
- 其他: 不变

### 零件类型标注示例

| 原始 类型 | spec | 拆分后 web | 拆分后 flange |
|-----------|------|-----------|--------------|
| `"H钢"` | `BH300*200*6*8` | `"H钢BH腹"` | `"H钢BH翼"` |
| `""` (空) | `BH300*200*6*8` | `"BH腹"` | `"BH翼"` |
| `"箱型"` | `BOX650*300*14*24` | `"箱型BOX腹"` | `"箱型BOX翼"` |
| `"T钢"` | `BT150*100*5*6` | `"T钢BT腹"` | `"T钢BT翼"` |

### 数量列变更

| 类型 | web qty | flange qty | 逻辑 |
|------|:-------:|:----------:|------|
| BH/HA | N | N×2 | 1 腹板 + 2 翼缘 / 截面 |
| I/HI | N | N×2 | 同上 |
| BT | N | N | 1 腹板 + 1 翼缘 / 截面 |
| **BOX** | **N×2** | N×2 | **2 腹板 + 2 翼缘 / 截面** |
| PL | N | — | 仅排序，qty 不变 |

---

## 完整模块 API

```
multi_split/
├── profile.py      # 核心拆分 (split_profile_df / split_profile_excel)
├── bom.py          # 构件 BOM 生成 (qdmade)
├── sort.py         # 多条件排序 (multisort)
├── fill.py         # 向下填充 (fillin)
├── combination.py  # 等条件合并 (combination_check / combination_merge)
├── crossref.py     # 两表对照 (mddzb)
├── txt_import.py   # SELX TXT 导入 (transtxt)
├── io.py           # Excel 读写 (read_excel / write_excel)
├── models.py       # 数据模型 (SortSpec / ColumnMapping)
├── config.py       # 配置 (SunFireConfig)
├── utils.py        # 工具函数 (detect_data_region 等)
└── cli.py          # CLI (可选, 需 click)
```

### `split_profile_df` / `split_profile_excel`
核心拆分，见上。

### `qdmade(df, other_cols, unique_cols, column_mapping, config, header_row)`
零件清单 → 构件 BOM。自动匹配 12 关键词 (图号/构件号/构件数量/零件号/规格/宽度/长度/材质/零件总数/总重/零件类型/制作单位)，识别主材 + 附件，合并型钢规格。

### `fillin(df)`
空白单元格向下填充 (`=R[-1]C`)。VBA `fillin` 子程序直译。

### `multisort(df, sort_specs)` / `multisort_from_strings(df, strings)`
多条件排序，最多 5 条件。传入 `SortSpec(column, ascending)` 或 `"column:asc"` 字符串。

### `combination_check(df, baseline_col, check_cols)` / `combination_merge(df, condition_cols, sum_cols)`
等条件行的检查 + 合并求和。

### `mddzb(source_df, target_df, standard_cols, content_cols)`
两表对照合并 (outer join)，目标列加 `目标-` 前缀。

### `transtxt(file_paths, quantities, encoding="gbk")`
SELX 导出 TXT 导入，GBK 编码，tab+空格分隔。

### `read_excel(path, sheet_name, header_row)` / `write_excel(df, path, sheet_name, column_styles)`
Excel I/O，可选列样式 (openpyxl Font)。

### `SunFireConfig` / `ColumnMapping` / `SortSpec`
配置与数据模型。`SunFireConfig.from_yaml(path)` 支持 YAML (需 pyyaml)。

---

## 配置

```python
from multi_split import SunFireConfig, ColumnMapping

config = SunFireConfig()
config.attachment_keywords  # ["连接板", "附件", "散件"] — 附件检测关键词
config.main_material_keyword  # "主" — 主材检测关键词

# 12 关键词列映射 (控制 qdmade 如何匹配 Excel 列)
mapping = ColumnMapping(
    spec="规格", total_weight="总重", part_type="零件类型", ...
)
```

---

## 注意事项

1. **仅 .xlsx** — 不支持 .xls (97-2003)。Tekla 导出的 "伪 .xls" (TSV+GBK) 需先用 `transtxt` 或 `pd.read_csv(sep="\t", encoding="gbk")` 导入。
2. **列名灵活** — 支持精确匹配 / 子串匹配 / 0-based 索引。例 `spec_col="截面型材"` 可匹配 Tekla 原始列名。
3. **无 Excel 依赖** — 纯 openpyxl + pandas，Linux/macOS/Windows 均可。
4. **数量列非数字** — 文本值跳过乘法，保持原值。NaN 产生 `"nan"` 字符串 (建议上游清洗)。
5. **BOX 非默认** — 需显式 `modes=["BOX"]`；也可加 `"BOX"` 到 `DEFAULT_MODES`。
6. **PyYAML / click 可选** — 不用 YAML 配或 CLI 则无需安装。
