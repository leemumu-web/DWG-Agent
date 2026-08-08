# 变更记录

## 日期：2026-07-25

## 需求来源

### 第一批（`追加更改及测试结果中的错误.txt`）

1. 修正测试结果 Excel（BH/BOX_拆板后_分类结果.xlsx）第 3 列标注的错误
2. BH 翼板命名为 "xx翼-1"/"xx翼-2"（非"上下翼"）时，跳过折弯判断（WITHOUT_BEND）
3. 不考虑孔的大小，只要外闭合轮廓内部存在闭合轮廓，就判定为"有孔"（`has_internal_holes` 增加 LINE/LWPOLYLINE 闭合环检测）

### 第二批（BH 分类结果漏检孔洞 + 回退翼板编号规则）

- 测试数据路径变更为：
  - BH: `F:\项目agent1\BH测试\项目1\02_拆板结果\auto_accepted\`
  - BOX: `F:\项目agent1\BOX测试\2_拆板结果\`
- BH 腹板分类错误：大量腹板漏检孔洞（判为"方"/"异"，实际应为"方孔"/"异孔"）
- 回退需求②：取消"翼板编号命名跳过折弯"的规则，所有翼板统一继承腹板折弯状态

---

## 修改文件清单

### 1. `src/yikongzhe/dxf_reader.py`

| 序号 | 位置 | 修改内容 | 原因 |
|------|------|----------|------|
| ① | L81-108 | 新增 `_SyntheticLine` 类 | 模拟 ezdxf LINE 实体，REGION 展开为边界线段时使用 |
| ② | L111-113 | 新增 `_Point` 数据类 | 轻量坐标点，供合成实体使用 |
| ③ | L117-139 | 新增 `_SyntheticCircle` 类 | 模拟 ezdxf CIRCLE 实体，REGION 完整圆形边（螺栓孔）标记 |
| ④ | L142-156 | 新增 `_explode_region_to_entities()` | 将 REGION 展开为 LINE + 合成 CIRCLE 列表（替代单纯 LINE 展开） |
| ⑤ | L159-201 | 新增 `_extract_circular_holes_from_region()` | 从 REGION 中检测完整圆形边（start_param≈0, end_param≈2π），创建合成 CIRCLE |
| ⑥ | L202-248 | `_explode_region_to_lines()` 函数头补回 | 上次编辑时函数头意外丢失，导致 BOX 文件全部报错 |
| ⑦ | L416 | `_explode_region_to_lines(e)` → `_explode_region_to_entities(e)` | 使合成 CIRCLE 进入实体管线，被 `has_internal_holes` 检测到 |
| ⑧ | L505-518 | `_assign_entities_by_connectivity()` 中 `line_like_entities` 收集范围增加 ARC | 防止大圆弧被距离分配误分到不相邻的板件 |

### 2. `src/yikongzhe/geometry.py`

| 序号 | 位置 | 修改内容 | 原因 |
|------|------|----------|------|
| ① | L76-79 | `extract_outer_contour()` 中增加 `poly.buffer(0)` 回退 | 修复自相交导致多边形无效的问题（如 h-3-cb-53 腹板） |
| ② | L373-432 | `has_internal_holes()` 增加 LINE 闭合环检测 | 使用图论方法检测外轮廓内部的 LINE/LWPOLYLINE 闭合环，不限于 CIRCLE/ARC 中心点 |
| ③ | L427 | 闭合环检测使用 `contains_properly()` | `shapely.contains()` 对相等多边形返回 True，导致所有零件误判为有孔 |

### 3. `src/yikongzhe/classifier.py`

| 序号 | 位置 | 修改内容 | 原因 |
|------|------|----------|------|
| ① | 文件顶部 | 新增 `import re` | 用于正则匹配翼板编号命名 |
| ② | 新增函数 | `_is_numbered_wing(name)` | 判断翼板是否命名为 "翼-1"/"翼-2" 格式 |
| ③ | L158-163 附近 | 翼板处理：若为编号翼板，`bend = BendType.WITHOUT_BEND` | 需求②：编号翼板应跳过折弯判断 |

---

## 修复的错误明细

### BH（腹板类）— 原始 2 个错误，全部修复

| 零件 | 原分类 | 修正后 | 根因 |
|------|--------|--------|------|
| p=2b1-cb-35腹 | 异 | 异孔 | 大圆弧被距离分配误分到翼板-2（X 坐标更近），改用连通性分配后修复 |
| p=h-3-cb-53腹 | 异 | 异孔 | 外轮廓自相交导致 Polygon 无效 + ARC 连通性问题，buffer(0) + 连通性修复 |

### BOX（箱型类）— 原始 3 个错误（1 个重复），全部修复

| 零件 | 原分类 | 修正后 | 根因 |
|------|--------|--------|------|
| p=h-3-cb-2腹 | 异 | 异孔 | 8 个 REGION 螺栓孔为完整圆形（start_vertex == end_vertex），展开为 0 条 LINE。新增合成 CIRCLE 检测后修复 |
| p=2b1-cb-35腹 | 异 | 异孔 | 同 BH |
| p=h-3-cb-53腹 | 异 | 异孔 | 同 BH |

### 翼板编号命名 — 新增需求

| 零件 | 行为 |
|------|------|
| xx翼-1 / xx翼-2 | 自动跳过折弯判断，BendType = WITHOUT_BEND |
| xx上翼 / xx下翼 | 保持原有逻辑（跟随腹板折弯状态） |

---

## 测试验证结果

```
BH:  42/42 OK, 0 mismatches
BOX: 63/63 OK, 0 mismatches
```

测试数据路径：
- BH DXF: `F:\项目agent1\BH拆板前后数据\BH_拆板后_dxf\`
- BOX DXF: `F:\项目agent1\BOX拆板前后数据\BOX_拆板后_dxf\`
- 预期结果: `BH_拆板后_分类结果.xlsx` / `BOX_拆板后_分类结果.xlsx`（第 3 列为修正值）

---

## 关键技术要点

1. **shapely.contains() vs contains_properly()**：`contains()` 对相等多边形返回 True，检测内部孔洞必须用 `contains_properly()`
2. **ACIS/SAB 圆形边**：REGION 中完整圆形（start_param=0, end_param≈2π）的起点和终点重合，`_explode_region_to_lines` 会跳过。需单独提取为合成 CIRCLE 标记
3. **ARC 连通性**：`_assign_entities_by_connectivity` 现在将 ARC 纳入 LINE 连通图（取弧端点），防止大圆弧被距离分配误分
4. **无效多边形**：`buffer(0)` 可以修复大多数自相交多边形，使 `is_valid` 变为 True

---

## 第二批修改（2026-07-25，同日）

### 问题根因

腹板孔洞漏检：CIRCLE 实体（CUT_HOLE 图层）按加权二维距离（Y权重3x）就近分配给 TEXT 标签，忽略了**几何轮廓包含关系**。翼板的 TEXT 标签在 Y 方向更近，导致腹板轮廓内的 CIRCLE 被误分给翼板，腹板外轮廓内无 CIRCLE 残留，`has_internal_holes()` 返回 False。

示例（w3-cb-17）：腹板是宽矩形，20 个 CIRCLE 中心在腹板轮廓内，但 10 个被分给翼缘板-1（TEXT Y=300 vs 腹板 TEXT Y=1188）、10 个被分给翼缘板-2。腹板因此被错判为"方"（实际应为"方孔"）。

### 4. `src/yikongzhe/dxf_reader.py`

| 序号 | 位置 | 修改内容 | 原因 |
|------|------|----------|------|
| ① | L663-717 | 新增 `_reassign_entities_by_contour(parts)` | 轮廓二次校验：初始分配后，提取各板外轮廓，将 CIRCLE/ARC 中心归入包含其轮廓的板件 |
| ② | L479-482 | `_associate_entities_to_texts()` 末尾调用 `_reassign_entities_by_contour()` | 在连通分量 + 距离分配完成后执行轮廓重分配 |

### 5. `src/yikongzhe/classifier.py`

| 序号 | 位置 | 修改内容 | 原因 |
|------|------|----------|------|
| ① | 删除函数 | 移除 `_is_numbered_wing(name)` | 回退编号翼板跳过折弯的规则 |
| ② | 删除导入 | 移除 `import re` | 不再需要正则匹配 |
| ③ | 翼板处理 | 统一 `bend = BendType.WITH_BEND if web_has_bend else BendType.WITHOUT_BEND` | 所有翼板（含 xx翼-1/xx翼-2）均继承腹板折弯状态 |

### 修复错误明细（第二批）

BH 腹板类 — 用户检查 w3-cb-1 ~ w3-cb-31，标记 18 个错误，全部修复：

| 零件范围 | 原分类 | 修正后 | 根因 |
|----------|--------|--------|------|
| w3-cb-16腹 | 异 | 异孔 | CIRCLE 被错分到翼板，轮廓重分配后修复 |
| w3-cb-17~31腹 | 方 | 方孔 | 同上（15 个腹板） |
| w3-cb-1腹 | 异 | 异孔 | 同上 |
| w3-cb-2腹 | 异 | 异孔 | 同上 |

### 测试验证结果

```
BH:  用户标记 18/18 OK, 0 差异
BOX: 32 DXF, 64 板（未做人工校验）
```
