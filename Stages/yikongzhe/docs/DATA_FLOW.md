# 数据流文档

## 整体流程

```
用户指定目录
      │
      ▼
┌─────────────────────────────────────────────┐
│                CLI入口 (__main__.py)          │
│  解析参数 → classify_directory() → write_excel() │
└─────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────┐
│           classify_directory()                │
│  遍历 *.dxf → read_dxf() → classify_dxf()    │
└─────────────────────────────────────────────┘
      │
      ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  dxf_reader  │    │  classifier  │    │ excel_writer │
│  .read_dxf() │───▶│ .classify_   │───▶│ .write_excel()│
│              │    │  dxf()       │    │              │
└──────────────┘    └──────────────┘    └──────────────┘
```

## 各阶段数据转换

### 阶段1: DXF 解析

```
输入: .dxf 文件
  │
  ├── ezdxf.readfile() 打开文件
  ├── _explode_inserts() → 打散 INSERT 块引用（含变换矩阵，最多10层嵌套）
  ├── 遍历 modelspace → TEXT[layer∈{PartMark, OtherObjectType}] → 板件名称 + 坐标
  ├── 遍历 modelspace → LINE/CIRCLE/ARC/LWPOLYLINE/REGION → 几何实体
  │     └── REGION → _explode_region_to_lines() → ACIS(SAB) → LINE 线段
  ├── 构建 LINE 实体连通图（0.5mm snap 容差, Union-Find）
  ├── 连通分量按 Y 距离分配到板件
  │     └── 空板件再分配：同 Y 位置多板件时用 X 距离转移分量
  ├── 非 LINE 实体按加权二维距离（Y权重3x）就近分配
  │
  ▼
输出: list[Part]
  Part.name = "p=2b1-cb-18腹"
  Part.is_web = True
  Part.text_position = (6658.8, 4033.6)
  Part.entities = [LINE×17, ARC×130, ...]
```

### 阶段2: 几何分析（方/异 + 有孔/无孔）

```
输入: Part.entities
  │
  ├── _collect_edge_pairs() → 从 LINE/ARC/LWPOLYLINE 提取端点对
  ├── _snap_coord() → 坐标四舍五入到0.5mm
  ├── _build_snapped_graph() → 邻接图
  ├── _find_all_cycles() → DFS + 最左转策略找闭合环
  ├── 最大面积环 → 外轮廓 Polygon
  │
  ├── is_rectangle():
  │     去重后顶点数=4，对边等长(<2%)，四角≈90°(<1°)，
  │     对边反向平行(cos > -0.99)
  │     → True(方) / False(异)
  │
  ├── has_internal_holes():
  │     CIRCLE/ARC 中心在外轮廓 Polygon.contains() 内
  │     → True(有孔) / False(无孔)
  │
  ▼
输出: (ShapeType, HoleType)
```

### 阶段3: 折弯检测

```
输入: 腹板 Part
  │
  ├── extract_outer_contour() → 顶点序列（去闭合点）
  ├── _has_bend_signature():
  │     每边方向分类: angle<5°→H / angle>85°→V / else→D
  │     收集 H-D-H 模式斜边（前后边均为H, 长度>100mm）
  │     按角度分组平行斜边（容差5°）
  │     组内查找反向对（y_delta符号相反 → 一条上一条下）
  │     验证空间距离 ≤ bbox对角线×30%
  │     → 找到 → True
  │
  ▼
输出: bool (True=腹板有折弯特征 / False=无)
```

### 阶段4: 类别查表

```
输入: (ShapeType, HoleType, BendType)
  │
  ├── classify_dxf() 编排:
  │     腹板 → classify_part_shape_and_hole() + detect_bend()
  │     翼板 → classify_part_shape_and_hole() + 继承腹板bend
  │     腹板 bend 固定为 WITHOUT_BEND
  │
  ├── build_category_name():
  │     三要素 → 类别名称 (8种组合)
  │     (RECTANGLE, WITHOUT_HOLE, WITHOUT_BEND) → "方"
  │     (RECTANGLE, WITHOUT_HOLE, WITH_BEND) → "方折"
  │     ...
  │
  ▼
输出: PartClassification(category="方折", ...)
```

### 阶段5: Excel 输出

```
输入: list[DxfResult]
  │
  ├── Sheet "分类结果": 板件名称 | 图形类别
  ├── Sheet "统计": 各类别计数（按8种类别固定顺序）
  │
  ▼
输出: .xlsx 文件
```

## 关键设计决策

### 实体分配：连通分量 + Y 距离 + 再分配

同一 DXF 中的多个板件通常垂直堆叠（不同 Y 坐标）。算法先构建 LINE 实体连通图，
每个连通分量按 Y 距离分配给最近的 TEXT。当同 Y 位置有多个板件（如左右翼板），
通过 X 距离再分配机制将空板件从多余板件获取实体。

### REGION (ACIS) 实体处理

部分 DXF 中的板件几何以 REGION 实体存储（ACIS/SAB 二进制格式）。
解析 body→lump→shell→face→loop→coedge→edge→vertex 层次结构，
取每条边的端点连线近似所有曲线类型（直线/圆弧/样条），将 REGION 展开为 LINE 线段参与后续处理。

### 外轮廓提取：图论 + 最左转策略

LINE 端点有浮点偏差，shapely 的 polygonize 要求精确匹配。
改用 snap 容差（0.5mm）构建邻接图 + DFS 找环，始终选"最右转"边前进
（等价于沿外轮廓逆时针行走），稳健性更好。

### ARC 作为边参与轮廓构建

腹板轮廓由 LINE 通过 ARC 角部圆角连接。LINEs 单独不构成闭合环。
将 ARC 端点也加入边对列表，图遍历可经过 ARC 连接 LINE 到完整的闭合轮廓。

### 折弯检测：配对斜边 + 反向垂直跳变

折弯板件的腹板呈"阶梯形"：存在两条平行斜边，一条使轮廓向上跳变、
另一条向下跳变，形成完整的阶梯结构。

- 折弯对角线：>100mm，前后均为水平边，成对出现且垂直方向相反
- 倒角对角线：夹在水平和竖直边之间，不成对或垂直方向相同

配对距离限制为板件对角线长度的 30%，确保两条斜边在空间上足够接近。