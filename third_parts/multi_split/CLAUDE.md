# multi_split — Agent 对接指南

## 用途

对 Excel 中的钢结构零件表进行型材/板材自动拆分。H 型钢、工字钢拆为腹板+翼缘，板材按厚度×宽度排序。

## 入口点 (两个)

### 1. Excel 文件级别 — 首选

```python
from multi_split import split_profile_excel

split_profile_excel(
    excel_path,            # str: .xlsx 文件路径 (必填)
    sheet_name="整理表",   # str: 要处理的子表名
    spec_col="规格",       # str: 规格列名
    width_col="宽度",      # str: 宽度列名
    qty_col="数量",        # str: 数量列名
    part_type_col="零件类型",  # str: 零件类型列名
    modes=None,            # list | None: None=全部 ['BH','I','PL'], 可选子集
    output_sheet=None,     # str | None: None="{sheet_name}_拆板后"
)
# 返回: str — 新创建的子表名
```

**行为**：读取 `sheet_name` → 拆分 → 在同文件中新增 `{sheet_name}_拆板后` 子表。**原表完全不动**。

### 2. DataFrame 级别 — 用于链式处理

```python
from multi_split import split_profile_df

result_df = split_profile_df(
    df,                    # pd.DataFrame: 输入数据
    spec_col="规格",       # 同上
    width_col="宽度",
    qty_col="数量",
    part_type_col="零件类型",
    modes=None,            # None = ['BH', 'I', 'PL']
)
# 返回: pd.DataFrame — 拆分后的数据
```

**不涉及文件 I/O**，只做数据变换。

## 输入格式要求

### Excel 子表应有的列

| 列 | 含义 | 示例值 |
|----|------|--------|
| 规格列 | 型材规格字符串 | `BH300*200*6*8`, `PL10*2000`, `L50*5` |
| 宽度列 | 宽度/尺寸 | `300`, `10` (数字或文本均可) |
| 数量列 | 数量 | `1`, `5` |
| 零件类型列 | 零件分类标注 | `H钢`, `钢板`, 可为空 |

列名**不必完全匹配默认值**，agent 调用时通过 `spec_col=`, `width_col=` 等参数指定实际列名即可。

表头行自动检测：工具会扫描上半部分行，找非空率 ≥87.5% 的行作为标题行。标题行之上的总标题等会自动跳过。

### DataFrame 输入

直接传有列名的 `pd.DataFrame`，列名需与实际数据列名一致。若从 Excel 用 `pd.read_excel(header=None)` 读取原始数据，先用 `multi_split.utils.detect_data_region()` 处理。

## 什么被识别、什么不被识别

### 会被拆分

| 前缀 | 类型 | 拆分方式 |
|------|------|---------|
| `BH`, `HA` | H 型钢 | 1行→2行 (腹板+翼缘×2) |
| `I`, `HI` | 工字钢 | 1行→2行 (腹板+翼缘×2) |
| `BT` | T 型钢 | 1行→2行 (腹板+翼缘×1) |
| `PL`, `-` | 板材 | 1行→1行 (厚度×宽度排序) |

### 不会被拆分 (原样保留)

`HN`, `HW`, `HM`, `L`, `C`, `方管`, `D`, 纯数字, 等 — 前缀不在识别集合中的都原样通过。

## 输出格式

Agent 会拿到的输出 DataFrame (或在 Excel 新子表中) **比输入多出若干列**:

| 新增内容 | 说明 |
|---------|------|
| `拆分标记` 列 | 仅当存在拆分行时出现。被拆分的行值为 `"拆"`，未拆分的为空 |

**行数变化**：每个 BH/I/BT 行变为 2 行 (腹板+翼缘)，PL 行数不变，非识别行不变。

### 拆分后的零件类型列 (part_type_col) 标注

| 原始值 | 拆分后 |
|--------|--------|
| `"H钢"` | `"H钢H腹板"`, `"H钢H翼缘"` |
| `""` (空) | `"H腹板"`, `"H翼缘"` |
| `"工钢"` | `"工钢工腹板"`, `"工钢工翼缘"` |
| `"钢板"` (PL) | `"钢板"` (不变) |

### 数量列的变更

| 类型 | 腹板数量 | 翼缘数量 |
|------|:------:|:------:|
| BH/HA | N | N×2 |
| I/HI | N | N×2 |
| BT | N | N |
| PL | N (不变，不拆分) | — |

## 典型调用流程

```
用户给 agent 一个 Excel 文件 + 要处理的子表名
        ↓
agent 先用 pd.ExcelFile 查看有哪些子表、列名是什么
        ↓
agent 调用 split_profile_excel(
    excel_path="用户给的文件.xlsx",
    sheet_name="用户指定的子表",
    spec_col="实际规格列名",    ← 根据实际列名填写
    width_col="实际宽度列名",
    qty_col="实际数量列名",
    part_type_col="实际类型列名",
)
        ↓
返回新子表名, agent 告知用户处理完成
```

## 注意事项

1. **原文件被修改** — `split_profile_excel` 会直接写入原 `.xlsx` 文件，新增一个子表。如需保护原始文件，先 copy。
2. **输出子表覆盖** — 若目标子表名已存在，会被覆盖。
3. **编码** — 中文字段名完全支持，openpyxl 底层处理。
4. **不需要 Excel 安装** — 纯 Python，openpyxl 读写 `.xlsx`。
5. **仅支持 .xlsx** — 不支持 `.xls` (97-2003 格式)。
6. **数量列非数字** — 若数量列是文本，翼缘翻倍计算会被跳过，数量保持原值。
