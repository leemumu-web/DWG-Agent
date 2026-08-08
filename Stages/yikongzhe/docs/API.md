# API 接口文档

## 模块概览

| 模块 | 职责 | 主要入口 |
|------|------|---------|
| `models.py` | 数据模型定义 | `Part`, `PartClassification`, `DxfResult` |
| `dxf_reader.py` | DXF文件解析 | `read_dxf()`, `read_dxf_directory()` |
| `geometry.py` | 几何分析 | `extract_outer_contour()`, `is_rectangle()`, `has_internal_holes()` |
| `bend_detector.py` | 折弯检测 | `detect_bend()` |
| `classifier.py` | 分类编排 | `classify_dxf()`, `classify_directory()` |
| `excel_writer.py` | Excel输出 | `write_excel()` |

---

## `models.py` — 数据模型

```python
class ShapeType(Enum):
    RECTANGLE = "方"     # 外轮廓为矩形
    IRREGULAR = "异"     # 外轮廓不规则

class HoleType(Enum):
    WITH_HOLE = "有孔"
    WITHOUT_HOLE = "无孔"

class BendType(Enum):
    WITH_BEND = "有折"
    WITHOUT_BEND = "无折"

@dataclass
class Part:
    name: str                              # 板件名称
    dxf_file: str                          # 来源 DXF 文件名
    is_web: bool                           # True=腹板, False=翼板
    text_position: tuple[float, float]     # TEXT 标注坐标
    entities: list                         # 几何实体列表

@dataclass
class PartClassification:
    part_name: str
    shape: ShapeType
    hole: HoleType
    bend: BendType
    category: str                          # 最终类别名，如 "方孔折"

@dataclass
class DxfResult:
    dxf_file: str
    parts: list[PartClassification]
```

---

## `dxf_reader.py` — DXF 解析

### `read_dxf(filepath, *, encoding='utf-8') -> list[Part]`

解析单个 DXF 文件，返回板件列表。

**处理流程:**
1. 打散 INSERT 块引用（最多10层嵌套）
2. 从图层 `PartMark` / `OtherObjectType` 提取 TEXT 实体得到板件名称
3. 从图层 0 提取 LINE/ARC/CIRCLE/LWPOLYLINE/REGION 几何实体
4. REGION（ACIS/SAB 数据）解析为 LINE 线段后参与后续处理
5. 构建 LINE 实体连通图（0.5mm snap 容差），按连通分量 + Y 距离将实体分配到板件
6. 当同一 Y 位置有多个板件时，自动用 X 距离再分配空板件的分量

**参数:**
- `filepath`: DXF 文件路径 (str | Path)
- `encoding`: DXF 编码，默认 utf-8

**返回:** Part 对象列表

**异常:** `FileNotFoundError`, `ezdxf.DXFError`

### `read_dxf_directory(directory, *, encoding='utf-8') -> list[list[Part]]`

遍历目录下所有 `*.dxf` 文件并解析，返回二维列表。

---

## `geometry.py` — 几何分析

### `extract_outer_contour(entities) -> tuple[list, Polygon|None]`

从几何实体中提取外轮廓。使用图论方法：

1. 从 LINE/ARC/LWPOLYLINE 收集端点对
2. 坐标 snap（0.5mm 容差）后构建邻接图
3. DFS + "最左转"策略找闭合环
4. 面积最大的环即为外轮廓

**返回:** `(顶点列表, shapely Polygon)` 或 `([], None)`

### `is_rectangle(contour, *, tolerance=1.0) -> bool`

判断轮廓是否为矩形：

- 去重后顶点数 == 4
- 对边长度相等（相对偏差 < 2%）
- 相邻边夹角 ≈ 90°（偏差 < tolerance 度）
- 对边向量反向平行（cos > -0.99）

### `has_internal_holes(outer_contour, all_entities) -> bool`

判断外轮廓内部是否有孔洞：

- 用外轮廓构建 Polygon
- 检查 CIRCLE/ARC 实体的中心是否在外轮廓内部
- 中心在内即判定为有孔（螺栓孔由两个半圆 ARC 组成，圆心在轮廓内）

---

## `bend_detector.py` — 折弯检测

### `detect_bend(web_part: Part) -> bool`

检测腹板是否存在折弯特征。

**算法（配对斜边 + 反向垂直跳变）:**

1. 提取腹板外轮廓顶点序列
2. 分析每条边的方向类型：angle<5°→H(水平), angle>85°→V(竖直), 其余→D(对角)
3. 找出所有 H-D-H 模式的斜边（前后边均为水平边，长度 > 100mm）
4. 将近似平行的斜边分组（角度容差 5°）
5. 在每组内查找"反向对"：一条 y_delta 向上、另一条 y_delta 向下
6. 验证配对斜边的空间距离 ≤ 板件对角线长度的 30%

这种配对斜边形成完整的"阶梯"结构，区别于角落的倒角（仅出现在水平和竖直边之间）。

**返回:** True 表示腹板存在折弯特征

---

## `classifier.py` — 分类编排

### `classify_dxf(parts: list[Part]) -> DxfResult`

对单个 DXF 文件的所有板件执行三步分类：

1. 分离腹板（`is_web=True`）和翼板（`is_web=False`）
2. 对腹板执行 `classify_part_shape_and_hole()` 得到 shape + hole
3. 对腹板执行 `detect_bend()` 得到折弯状态
4. 对翼板执行 `classify_part_shape_and_hole()` 得到 shape + hole
5. 翼板继承腹板的折弯状态
6. 腹板的 bend 固定为 `WITHOUT_BEND`
7. 查表 `build_category_name()` 得到最终类别名

### `classify_directory(directory, *, encoding='utf-8') -> list[DxfResult]`

遍历目录下所有 DXF 文件并执行分类。

### `build_category_name(shape, hole, bend) -> str`

三要素查表（8种组合）→ 类别名称。

---

## `excel_writer.py` — Excel 输出

### `write_excel(results: list[DxfResult], output_path: str) -> None`

输出 `.xlsx` 文件，包含两个 Sheet：

- **分类结果**: 板件名称 | 图形类别
- **统计**: 各类别计数（按8种类别固定顺序排列）