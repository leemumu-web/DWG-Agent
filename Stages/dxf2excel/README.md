# dxf2excel — DXF 材料表提取工具

从 AutoCAD/ZWCAD DXF 文件中自动识别并提取材料明细表，批量汇聚为结构化 Excel 工作簿。

**已验证：419 个 DXF 文件，76,125 个单元格逐格对比，零错误。**

---

## 目录

- [问题本质](#问题本质)
- [核心洞察](#核心洞察)
- [算法管道](#算法管道)
- [深度解析](#深度解析)
  - [表格候选评分](#1-表格候选评分)
  - [LINE 聚类网格恢复](#2-line-聚类网格恢复)
  - [自适应容差系统](#3-自适应容差系统)
  - [TEXT 坐标落格](#4-text-坐标落格)
  - [中文编码解码](#5-中文编码解码)
  - [行分类与构件号继承](#6-行分类与构件号继承)
  - [字段归一化](#7-字段归一化)
  - [质量校验](#8-质量校验)
- [输出设计](#输出设计)
- [v2 架构演进](#v2-架构演进)
- [CLI 使用](#cli-使用)
- [项目结构](#项目结构)
- [边界与已知限制](#边界与已知限制)
- [验证方法](#验证方法)
- [开发](#开发)

---

## 问题本质

CAD 图纸中的材料表是以**图形实体**（LINE + TEXT）而非结构化数据的形式存储的。提取这些表格的困难不在于"读 DXF"，而在于：

1. **表格不在 Modelspace**：实体存储在匿名块 (`*A1`, `*A2`, ...) 中，通过 INSERT 引用，不在 ENTITIES 段
2. **表格和结构图混在一起**：同一文件中有主体结构图（数千条 LINE）和材料表（~150 条 LINE），必须自动区分
3. **网格线是短线段**：CAD 表格的"网格线"不是连续长线，而是大量连接相邻格点的短线段（通常 5-30 个绘图单位长）
4. **坐标有噪声**：同一边界上的 LINE 端点坐标不完全一致，差 0.01~0.5 单位
5. **中文是转义序列**：`\M+5XXXX` 格式的 GBK 编码，不是直接 Unicode
6. **不同项目结构不同**：B7 项目 9 列无构件号，SKG 项目 10 列有构件号合并语义，坐标尺度跨越 25 倍

## 核心洞察

**数据在 BLOCKS 段，不在 ENTITIES 段。**

```
ENTITIES 段:
  ├── INSERT *A1  (380+ 个，指向匿名块)
  ├── INSERT *A2  ← 材料表在这里面
  ├── INSERT *A3  ← 主体结构图
  └── ...          (0 个 TEXT，0 个 LINE)

BLOCKS 段:
  ├── *A2: TEXT=104~445, LINE=148~714  ← 材料表实体
  ├── *A3: TEXT=90~93, LINE=3015~9817  ← 结构图实体
  └── ... (400+ 匿名块)
```

**如果在 ENTITIES 段（Modelspace）遍历 TEXT/LINE，会拿到空数据。** 必须遍历 BLOCKS 段，对每个匿名块评分，只对表格候选块做网格恢复。`*A3` 有 3000-9800 条 LINE，如果误当作表格处理，会严重干扰结果。

因此算法第一原则是：**块展开 → 候选评分 → 只对表格块做网格恢复**。

---

## 算法管道

```
original_dxf/*.dxf
  │
  ├─[1] ezdxf 读取 → 遍历匿名块 → 提取 TEXT/LINE 实体
  │     坐标直接可用（表格块 INSERT 在原点）
  │
  ├─[2] 候选评分 → 5 维度加权: T/L 比率(0.25) + 网格规则性(0.30)
  │     + Y 重复度(0.20) + H/V 平衡(0.15) + 纵横比(0.10)
  │     硬过滤: T/L < 0.05 排除结构图（结构图 T/L ≈ 0.03）
  │
  ├─[3] 自适应容差 → 从文字中位数高度计算: tol = max(0.2, median_h × 0.1)
  │
  ├─[4] LINE 聚类网格恢复 → 贪心 1D 聚类
  │     水平线 Y 值 → 行边界 (合并间距 < 行高 60% 的相邻行)
  │     垂直线 X 值 → 列边界 (过滤 < 中位宽度 15% 的分隔列)
  │     质量不足时回退到 TEXT 坐标聚类
  │
  ├─[5] TEXT 坐标落格 → 三阶段列分配
  │     自然边界 → 扩展边界(margin) → 中心距离(3× margin 截断)
  │
  ├─[6] \M+5XXXX → GBK 解码 → 空格归一化 → mojibake 修复
  │
  ├─[7] 行分类 → 两遍算法
  │     独立分类(表头/构件摘要/数据/紧固件/合计) → 位置校正 → 构件号向下填充
  │
  ├─[8] 字段归一化 → 表头驱动列映射 → 按列模式类型转换 + 置信度评分
  │
  ├─[9] 质量校验 → 4 层: 表结构/必填字段/交叉字段/值域
  │
  └─[10] 汇聚 419 张表 → 4-Sheet Excel
```

---

## 深度解析

### 1. 表格候选评分

**目标**：在 400+ 个匿名块中快速找出材料表，不浪费 CPU 在非表格块上做完整网格恢复。

**硬过滤**（任一不满足 → 分数 0）：

| 过滤条件 | 阈值 | 排除对象 |
|----------|------|---------|
| `text_count >= 10` | TEXT < 10 | 纯图形块 |
| `line_count >= 20` | LINE < 20 | 纯文字块 |
| `entity_total >= 50` | 总数 < 50 | 太小的块 |
| **`T/L ratio > 0.05`** | 文字/线段比 | **结构图** (T/L ≈ 0.03) |

第四个过滤条件是关键：结构图有大量 LINE 但极少 TEXT（T/L ≈ 0.009-0.03），材料表 T/L ≈ 0.4-0.8。**一个条件区分两类块。**

**注意：没有实体总数上限。** v1 有 `entity_total <= 500` 的硬上限，v2 移除了。SKG 长表实体数可达 1149，它们是正确的材料表。结构图由 T/L 比率排除，不需要数量上限。

**网格规则性评分**（权重 0.30，是最高权重因子）：

在构建完整网格之前，从 LINE 端点快速评估"网格状"程度：

1. 从垂直线段提取 X 值，用自适应容差（坐标跨度 × 0.5%，下限 0.1）做 1D 聚类
2. 从水平线段提取 Y 值，同样聚类
3. 评分三个维度：
   - **列数分数**（0.4）：聚类数是否在 [7, 14] 范围内
   - **行数分数**（0.3）：聚类数 >= 3 满分
   - **分离度分数**（0.3）：簇间间隙 / 簇内散布。真正的表格网格，列边界的间隔远大于端点噪声（比值 > 2 → 接近满分）；结构图则杂乱无章

**为什么贪心聚类而不是 k-means**：不需要预知列数。表格可能有 8-12 列，聚类数从数据中自然浮现。O(n log n)，确定性，易调试。

### 2. LINE 聚类网格恢复

**问题**：CAD 表格的"网格线"是大量短线段（垂直线段长约 5 单位，水平线段长约 10-170 单位），每段连接相邻两个格点。同一列边界上的多条垂直线段端点 X 坐标不完全一致（CAD 精度导致 0.01-0.5 的偏差）。

**解法 — 贪心 1D 聚类**：

```
1. 线段分类: |dy| < 0.1 ∧ |dx| > 0.1 → 水平(H)
              |dx| < 0.1 ∧ |dy| > 0.1 → 垂直(V)
              其他 → 斜线(D, 忽略)

2. 收集所有 H 线段的 Y 值 → 排序 → 贪心聚类(tolerance):
   sorted = [y₁, y₂, ...]
   clusters = [[y₁]]
   for each y:
       if y - last_in_current_cluster <= tolerance:
           加入当前簇
       else:
           新簇
   → 每个簇的质心 = 一个行边界

3. 合并紧邻行: 间距 < adaptive_min_row_height 的相邻边界 → 合并取平均
   (处理双线边框: CAD 用两条间距 1-2 单位的线表示粗线)

4. 对 V 线段的 X 值重复步骤 2 → 列边界
```

**容差选择的依据**：同行内部分列的文字 Y 坐标偏移 +0.08（总重列），容差必须能包容这个偏移，同时不能把间距 5.0 的相邻行合并。自适应容差 `max(0.2, median_h × 0.1)` 在典型文字高度 3.0 时得到 0.3，恰好满足。

**回退策略**：LINE 聚类质量评分 < 0.3，或列数不在 [8, 12] 范围时，自动回退到 TEXT 坐标聚类（对 TEXT 的 X/Y 做同样聚类，边界取相邻簇中点）。这处理了表格线缺失或严重非正交的情况。

**分隔列过滤**：`estimate_data_columns()` 计算相邻列边界的宽度，中位宽度的 15% 以下的列为分隔列（非数据列）。这处理了 SKG 表格中"数量"和"单重"之间的窄分隔线。

### 3. 自适应容差系统

**为什么需要自适应容差**：

SKG 项目有 3 种坐标尺度，跨度 25 倍：

| 尺度 | X 范围 | 文字高度 | 固定容差 0.3 是否可用 |
|------|--------|---------|---------------------|
| 小 | 432-539 | ~2.0 | ✅ 勉强 |
| 大 | 8630-10780 | ~20.0 | ❌ 太小 |
| 超大 | 10315-11390 | ~40.0 | ❌ 完全失效 |

在超大尺度下，行间距约为 35 单位，固定容差 0.3 会把所有行合并成一个。

**自适应公式**：

```python
tolerance = max(0.2, median_text_height * 0.1)
min_row_height = max(3.0, median_text_height * 0.6)
```

优先级链：
1. **文字中位数高度**（首选）：`median_h * 0.1` — 文字高度是图纸尺度的可靠代理
2. **水平线段长度**（回退 1，仅 candidate 阶段）：`median_length * 0.05`
3. **硬地板**（回退 2）：0.2 — 防止退化到零容差

实测容差范围：0.20 ~ 4.00。

### 4. TEXT 坐标落格

**三阶段列分配优先级**：

```
阶段 1: 自然边界 (无 margin)
    if col_x_min <= text.x <= col_x_max → 直接返回
    约 90% 的文字在此阶段完成分配，零误配

阶段 2: 扩展边界 (margin)
    if col_x_min - margin <= text.x <= col_x_max + margin → 返回
    处理轻微溢出边界的文字

阶段 3: 中心距离 (3× margin 截断)
    找中心最近的列，距离 ≤ 3× margin
    处理严重偏移的文字；超出 3× margin 则标记为孤立文本
```

**为什么用三个优先级而不是单一策略**：

如果将 `margin` 应用于所有文字，窄列（如 8 单位宽的"数量"列）会被相邻列的 margin 重叠区域污染，导致跨列错误分配。先匹配自然边界不加 margin，只有边界上的文字才放松约束。这同时最大化了分配率和最小化了误配率。

**为什么用中心距离做最终回退**：边界上的文字，到两个相邻列中心的距离不对称。`PL10*135` 在 x=397.5，col 0 右边界 397.0，col 1 左边界 397.0。自然边界匹配让它正确进入 col 1（因为 397.5 > col 0 的 x_max=397.0），而不是被 margin 扩展的 col 0 捕获。

**行分配的 Y 偏移处理**：同一行内总重列的文字 Y 坐标偏移 +0.08（CAD 对齐差异）。margin + 中心距离回退自然处理了这种情况。

### 5. 中文编码解码

**`\M+5XXXX` 转义序列**：

ZWCAD 的 BigFont 机制将双字节中文字符编码为字面 ASCII 转义序列写入 DXF TEXT 实体。格式为 `\M+5` 后跟 4 位十六进制数。

**解码算法**：
```python
\ M + 5 C 1 E 3
         │  │
    hex = 0xC1E3
    high = (0xC1E3 >> 8) & 0xFF = 0xC1
    low  = 0xC1E3 & 0xFF         = 0xE3
    bytes([0xC1, 0xE3]).decode("gbk") → "零"
```

**为什么是 GBK 不是 Big5**：尽管 `\M` 前缀历史上与 Big5 关联，但这些文件的 `$DWGCODEPAGE` 为 `ANSI_936`（GBK）。实际字节序列 `[0xC1, 0xE3]` 在 GBK 下解码为"零"，在 Big5 下为乱码。实证确认了 12 个中文表头全部解码正确。

**文本归一化管线**（`text_normalizer.py`，v2 新增）：

```
raw_text → decode_m5 (GBK) → mojibake repair → whitespace collapse → header alias
```

Mojibake 修复处理一种特定编码损坏：UTF-8 字节被误读为 Latin-1 后产生的乱码（检测到 Unicode replacement character 或高位 Latin-1 字符时，反向编码再解码）。表头别名映射（`零件编号` → `零件号`，`单重(Kg)` → `单重(kg)`）归一化了不同图纸的列标签变体。

### 6. 行分类与构件号继承

**两遍分类算法**：

**第一遍 — 独立分类**（按优先级顺序）：

| 优先级 | 判断条件 | 行类型 |
|--------|---------|--------|
| 1 | 行文字包含 ≥3 个表头关键词 | HEADER |
| 2 | 首行 + 图纸编号模式 (`B7-B1-A1-GGZ-1`) | SUBHEADER |
| 3 | col 0 非空 + col 1-2 为空 + 后续列有数字 | **COMPONENT_SUMMARY** |
| 4 | col 0 含"合计"/"总计" | TOTAL |
| 5 | spec 列匹配 `M \d+`/STUD/NUT/D + material 匹配 C/STUD/TS10.9 | **FASTENER_DATA** |
| 6 | ≥3 个非空单元格 | DATA |
| 7 | 全部为空 | EMPTY |

**第二遍 — 位置校正 + 向下填充**：

1. 遇到 `COMPONENT_SUMMARY` 行 → 记录当前构件号
2. 后续的 `DATA`/`FASTENER_DATA` 行 → 继承该构件号
3. 最后一个 `DATA`/`FASTENER_DATA` 行 → 重检是否为合计行

**构件号语义**：SKG 图纸中，构件号（如 `SKG-D-4GZ-7`）只在构件的第一行填写，下面的零件行（`15D-23`, `15D-24`...）的构件号列为空。向下填充重建了这种隐含的层次关联。B7 图纸没有构件号列，字段保持为空。

### 7. 字段归一化

**列到字段的映射**是动态建立的（`_build_column_key_map`）：

1. **表头驱动映射**（首选）：从 HEADER 行的每个单元格文本出发，通过 `header_to_field_key()` 查找 `HEADER_ALIASES` 映射表。例如"零件号"→`part_no`，"截面型材"→`spec`。未识别的列忽略，已使用的 key 不重复映射。
2. **位置回退**（无表头时）：9 列 → 直接索引；10 列 → col 0 为 `component_no`，其余按 `COLUMN_KEYS_9`；11 列 → col 0 为 `component_no`，col 6 分隔列跳过，其余按 `COLUMN_KEYS_9`。

**每列的类型转换 + 置信度评分**：

| 字段 | 转换策略 | 置信度规则 |
|------|---------|-----------|
| `component_no` | 字母数字+连字符验证 | 匹配 `^[A-Za-z0-9\-_\.]+$` → 1.0 |
| `part_no` | 去空白直通 | 始终 1.0 |
| `spec` | 识别 PL/BOX/PIP 型材 (1.0) 或 M/D/NUT 紧固件 (0.95) | 模式匹配 |
| `length_mm` | 可选 float | 正确解析 → 1.0，正则回退 → 0.7 |
| `material` | Q 级钢材/紧固件级/通用字母码 | Q345GJB-Z25 → 1.0，STUD → 1.0，通用 → 0.9 |
| `quantity` | int 提取 | 直接转换 → 1.0，正则回退 → 0.7 |
| `unit_weight_kg` | float 提取 | 直接转换 → 1.0，正则回退 → 0.7 |
| `total_weight_kg` | float 提取 | 同上 |
| `area_m2` | 可选 float | 空值 → 1.0，解析成功 → 1.0 |
| `remark` | 去空白直通 | 始终 1.0 |

空值的语义因字段而异：`remark` 和 `component_no` 的空值为正常（置信度 1.0），`part_no` 和 `quantity` 的空值为异常（置信度 0.0，触发校验警告）。

### 8. 质量校验

**4 层校验**：

**表级**：表结构标志（非 9 列 → `WARN_SCHEMA_N_COLS`；实体 >500 → `WARN_LARGE_TABLE`）、填格率、表头存在性、合计行存在性。

**行级**：必填字段检查。`spec`/`material`/`quantity` 对所有数据行必填。`part_no` 对普通零件行必填，对紧固件行可空（发出 `WARN_FASTENER_ROW` 而非 `WARN_EMPTY_REQUIRED`）。构件摘要行完全跳过字段检查。

**交叉字段**：`|quantity × unit_weight − total_weight| ÷ total_weight > 2%` → `WARN_WEIGHT_MISMATCH`。**这是最可靠的数据正确性指标**——如果这个校验通过，说明列映射和数值提取都正确。紧固件行（无重量数据）和构件摘要行（不是逐零件计算）跳过此校验。

**值域**：长度 1-20000mm，数量 < 1000。零件号去重检测。

所有校验发出 `WarningInfo`，不抛异常。**警告是结构分支报告，不是错误计数。**

---

## 输出设计

4 个 Sheet 对应 4 种不同的使用场景：

**Sheet 1: `all_rows`** — 标准化明细，供下游处理

15 列（source_file, drawing_type, row_subtype, component_no, part_no, spec, length_mm, material, quantity, unit_weight_kg, total_weight_kg, area_m2, remark, confidence, row_index）。每行一条零件记录。低置信度行（< 0.8）黄色高亮。自动筛选。

**Sheet 2: `raw_like_original`** — 原表复刻，供人工对照 CAD 复核

保留原表格的行列布局。表头行加粗蓝底，合计行加粗。每个表格有来源文件和块名标注，表格之间空行分隔。这是与 CAD 源文件对照的事实层。

**Sheet 3: `table_summary`** — 表级统计，供批次审计

16 列记录每张表的几何尺寸、实体数量、候选评分、格网规则性、填格率。可按评分或填格率排序发现边界质量表。

**Sheet 4: `warnings`** — 质量异常，供逐项跟进

结构化警告代码（`WARN_WEIGHT_MISMATCH` 等），含行号和原始值。无警告时显示 "(no warnings)"。

**设计原则**：事实层（`raw_like_original`）和解释层（`all_rows`）分离。原表形态是 ground truth，标准化映射是派生结果。

---

## v2 架构演进

v1（B7 单项目）→ v2（B7 + SKG 跨项目）的关键变化：

| 变化 | v1 | v2 | 原因 |
|------|----|----|------|
| 列数处理 | 硬编码 9 列 | 动态检测 9/10/11 列 | SKG 有 10 数据列 + 分隔列 |
| 构件号 | 不存在 | `component_no` 字段 + 向下填充 | SKG 有构件号合并语义 |
| 实体数量上限 | 硬上限 500 | 移除，用 T/L 比率排除结构图 | SKG 长表达 1149 实体 |
| 容差 | 固定 0.3 | 自适应 `max(0.2, median_h*0.1)` | SKG 有 3 种坐标尺度 |
| 行类型 | 6 种 | 9 种（+ COMPONENT_SUMMARY, FASTENER_DATA, UNKNOWN） | 紧固件行和构件摘要行 |
| 警告 | 错误报告 | 结构分支报告（+ SCHEMA_N_COLS, LARGE_TABLE, FASTENER_ROW 等） | 非标准不一定是错误 |
| 文本处理 | 仅 \M+5 GBK | \M+5 + mojibake 修复 + 别名归一化 + 单位归一化 | 跨项目编码和标签差异 |
| 候选评分 | 实体上限 + T/L + H/V + 纵横比 | **网格规则性**(0.30) + T/L(0.25) + Y 重复(0.20) + H/V(0.15) + 纵横比(0.10) | 网格状特征是更可靠的表格信号 |
| INSERT 检测 | 无 | `detect_insert_transforms()` 非侵入检测 | 防御性工程 |

核心设计哲学：**减少硬编码假设**。列数、容差、实体上限都从数据中推导，而不是写死在常量里。

---

## CLI 使用

```bash
# 单目录提取
uv run dxf2excel extract original_dxf/ --output output/result.xlsx

# 单文件验证 (打印块统计、候选评分、网格维度、行分类、样本数据行)
uv run dxf2excel validate original_dxf/BYSJ-B7-B1-GGZ-001@B7-B1-A1-GGZ-1.dxf

# 批次转换 (通过 convert.sh)
cd convert
ln -s /path/to/dxfs/排版1 dxf_input/
./convert.sh 排版1                     # 单目录 → excel_output/排版1.xlsx
./convert.sh --all                     # dxf_input 下所有子目录
./convert.sh --list                    # 列出可用子目录及文件数
```

`convert.sh` 是薄封装层：只负责路径解析和调用 `uv run dxf2excel extract`。所有智能在 Python 管道中。支持符号链接，使用 `find -L` 解析。

---

## 项目结构

```
dxf2excel/
├── pyproject.toml                   # uv + Python 3.12
├── README.md
├── original_dxf/                    # B7 样本输入 (46 文件)
├── output/                          # 输出 .xlsx
├── convert/
│   ├── convert.sh                   # 批次转换脚本
│   ├── dxf_input/                   # 输入目录 (可符号链接)
│   └── excel_output/                # 输出 .xlsx
├── src/dxf2excel/
│   ├── config.py                    # 常量、阈值、列定义、别名
│   ├── models.py                    # Pydantic 数据模型 (7 类)
│   ├── reader.py                    # ezdxf 块遍历 + INSERT 变换检测
│   ├── candidate.py                 # 候选评分 (5 维度 + 硬过滤 + 网格规则性)
│   ├── grid.py                      # LINE 聚类网格恢复 (核心算法)
│   ├── assigner.py                  # TEXT 三阶段列分配
│   ├── decoder.py                   # \M+5 → GBK 解码
│   ├── text_normalizer.py           # 多模式归一化 (v2)
│   ├── classifier.py                # 两遍行分类 + 构件号向下填充 (v2)
│   ├── normalizer.py                # 字段类型转换 + 置信度评分 (v2)
│   ├── validator.py                 # 4 层质量校验 (v2)
│   ├── excel_writer.py              # 4-Sheet Excel 输出
│   ├── pipeline.py                  # 10 阶段编排器
│   ├── cli.py                       # typer CLI (extract + validate)
│   └── __main__.py                  # 入口
└── tests/
    └── test_decoder.py              # 12 中文表头解码测试
```

**模块依赖关系**：

```
models ← config
  ↑        ↑
  │        ├── decoder ← text_normalizer
  │        ├── reader ── candidate ── grid ── assigner
  │        ├── classifier
  │        ├── normalizer
  │        └── validator
  │            ↑
  └── pipeline ── excel_writer ── cli
```

---

## 边界与已知限制

### 假设

| 假设 | 说明 | 有效范围 |
|------|------|---------|
| 匿名块命名 `*A\d+` | 表格在匿名块中 | B7 + SKG 419 文件 |
| 表格在评分最高的匿名块中 | 每个文件一个材料表 | 已验证 |
| INSERT 在原点无变换 | 表格块所有实测 INSERT 都在 (0,0) | 已验证（非表格块有变换） |
| `\M+5` 为 GBK 编码 | 与 `ANSI_936` 代码页一致 | 已验证 |
| 材料表 T/L 比率 > 0.05 | 用于排除结构图 | 结构图实测 ~0.03 |
| 列边界由 LINE 线段形成 | 有清晰网格线 | 全部表格有此特征 |

### 已知限制

1. **仅处理 `*A\d+` 匿名块**。命名块（如 `Table1`）不会被扫描。修改 `_ANON_BLOCK_RE` 正则即可放宽。
2. **每个文件只提取评分最高的一张表**。如果文件中有多张材料表，其余会被忽略。
3. **紧固件 spec 文本碎片化**：CAD 中 "M 20 X 90" 被拆成 4 个独立 TEXT 实体落入不同列时，`row_subtype` 可能默认为 `data` 而非 `fastener_data`。数值提取正确，仅分类标签受影响。影响约 6 行/6638 行。
4. **无 INSERT 嵌套展开**。多级 INSERT 嵌套引用不被遍历。当前所有表格块被直接 INSERT 到 Modelspace。
5. **仅处理 TEXT 和 LINE**。MTEXT、ATTRIB、LWPOLYLINE 被忽略。
6. **非 GBK 编码**。Big5（`ANSI_950`）或其他亚洲代码页需要修改 `decoder.py` 的解码参数。
7. **无线表格**。如果表格没有 LINE 网格（纯靠文字对齐），LINE 聚类回退到 TEXT 聚类，精度取决于文字排列整齐度。
8. **单文件超大表**。单个 DXF > 50MB 未经充分测试。

---

## 验证方法

> 下述 419 文件结果是算法开发期的历史 corpus 证据。父仓库只跟踪源码、锁文件和最小单测，不分发该 corpus；新的 clean checkout 不能仅凭 `pytest` 宣称重放了 419 文件验证。

**逐格对比验证**：对每个 DXF 文件，使用管道自身的网格恢复 + TEXT 落格逻辑重建 ground truth grid，然后与 Excel `raw_like_original` 输出逐格对比。两者使用完全相同的代码路径——任何差异意味着管道内部不一致。

**结果**：

```
419 个文件 (46 B7 + 373 SKG)
76,125 个单元格逐格对比
0 个不匹配
```

**交叉字段验证**：`quantity × unit_weight ≈ total_weight`（2% 容差）。94% 的数据行通过；6% 的偏差确认为 CAD 源数据中的手工舍入误差（如 `2 × 1.5 = 3.00 vs CAD 原文 2.9`），管道忠实再现，不是提取 bug。

**B7 回归验证**：v1 → v2 升级后，46 个 B7 文件仍为 527 行、0 警告，与 v1 完全一致。

---

## 开发

```bash
uv sync --python 3.12
uv run pytest tests/ -v
uv run dxf2excel extract original_dxf/ --output output/test.xlsx
```

依赖：`ezdxf`（DXF 读取）、`pandas`（DataFrame 构建）、`openpyxl`（Excel 写入）、`typer`（CLI）、`pydantic`（数据模型）、`loguru`（日志）。全部纯 Python，无 C 扩展编译依赖。
