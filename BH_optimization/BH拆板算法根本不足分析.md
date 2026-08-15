# BH 拆板算法根本不足分析报告

> 研究对象：`Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/` 下的 `bh_*` 模块（约 2 万行）
> 源数据：`BH_拆板前_dxf汇总/`（352 张 Tekla BH 单件图 DXF）
> 结论：**这是一套"用自洽性证明冒充正确性验收"的手写规则引擎。它能可靠拦住"不完整/自相矛盾/几何非法"，却结构性无法拦住"内部自洽但业务拆错"的结果。**

---

## 1. 算法到底在做什么（架构）

一条 8 个顶层 pass 的确定性流水线（`bh_passes.py:680-689`）：

```
source.decode                        # ezdxf 读入，图元→事实 IR
source.normalize_and_partition       # 推断构件纵横轴/手性，切分视图区域
drawing.parse_and_associate_annotations # 建 DrawingGraph（视图/尺寸/孔标/件号/剖面）
drawing.resolve_component_metadata   # 从材料表文本正则解析 BH 截面/件号/长度/材质
hypotheses.solve_complete_component  # 枚举视图对→降低为板件→硬约束过滤/软约束打分
manufacturing.validate_assembly      # 复验几何不变量
manufacturing.freeze_ir_and_prove    # 冻结制造 IR + 追加证明义务 + 指纹
quality.route                        # 仅按"证明闭包"决定 auto/review/reject
```

求解器内部（`bh_solver.py`）是 **generate-and-test**：
- 枚举所有有序 `(腹板视图, 翼缘视图)` 对，用 bbox 与名义尺寸的残差 + 手调罚项（轴向罚 0.03、复杂度罚 0.002/0.0002）排序；
- 对每个候选调用 `lower_bh_assembly` 做约 19 步"降低"（`bh_solver.py:93-116`：web 种子面、腹板端部扩展、腹板隐藏线桥接、腹板微拓扑规则化、翼缘种子、翼缘展开长度推断、翼缘刚性延长、圆弧链恢复……）；
- 用 4 条硬规则 + 6 条软规则过滤/打分，选成本最低者。

## 2. 最根本的不足：auto_accept = 内部自洽，与真值零耦合

`auto_accept` 的判定（`bh_proofs.py:58-70`）：

```
auto_accept ⇔ 搜索完整 + 关键证明非空 + 无任何关键项 CONFLICT/INCOMPLETE/MISSING
```

而所有"关键证明义务"的证据，都来自**被选中的那个假设自己的** IR / 元数据 / 验证结果，没有一条来自外部真值。三条铁证：

1. 能力声明明文写着 `ground_truth_used_for_decision = False`（`bh_pipeline.py:92`）；
2. 人工拆板比对结果硬编码 `supervised_comparison_used_for_decision = False`（`bh_pipeline.py:412`）；
3. 生产入口 `pipeline.py:819` 传 `manual_reference_path=None`——人工拆板图（`*_拆板后.dxf`）在运行时**永不读取**。

真值比对函数 `compare_bh_to_manual`（`bh_compare.py:266-467`）确实存在，但只在离线检视命令 `steel-dxf-inspect --reference-dir` 里被调用（`layered_cli.py:265-266`），且结果同样标 `used_for_decision: False`。**仓库里甚至没有 BH 的拆板后真值目录**（只有 BOX 的 20 对在外部 `D:\DevData\`）。

因此整套"验证器"逐条归类后，**全部是自洽、无一条是真值**：

| 验证层 | 检查内容 | 性质 |
|---|---|---|
| `validate_bh_assembly`（14 条，`bh_validator.py:298-331`） | `one_web_plate` 只查 role=="web"（role 是 solver 自己赋的枚举→**同义反复**）；轮廓闭合/面积>0/孔不重叠/板厚=profile（对自解析 profile） | 几何良构 + 计数不变量 |
| `validate_bh_manufacturing_ir`（11 条，`bh_validator.py:145-201`） | IR 与 assembly 元数据/几何一致、**provenance 字段非空**（不验正确） | 字段自洽 |
| `validate_bh_saved_dxf`（24 条，`bh_validator.py:526-677`） | 输出的 DXF 能否重新打开、实体/图层/颜色/标签与 writer 期望一致 | 往返自洽（round-trip） |
| `build_proof_obligations`（~20 条，`bh_constraints.py:382-1309`） | 来源契约、分解计数、板厚=profile、轮廓拓扑、来源边守恒（**对自选边界**）、投影残差≤阈值、provenance 填满 | 自指证明 |

## 3. 为什么"自动通过的部分频频出错"

### 3.1 角色是"猜"的，验证器从不挑战"猜得对不对"

腹板/翼缘角色在 `view_pair` 枚举阶段就预先指派（`lower_bh_assembly(main=..., flange=...)`），后续所有证明都在**接受这个指派**的前提下进行。于是：

- **"腹板 vs 加劲板"误判几乎不可见**：`one_web_plate` 是恒真式；唯一把 web 与 profile 挂钩的 `web_not_smaller_than_minimum_clear_height` 只是 bbox 高度的松下限（`transverse+0.1 >= H-2tf`），一块足够大的加劲板/端板"碰巧"能通过；`flange_width_matches_profile` 只比 `min(bbox.w,bbox.h)` 与翼宽的 0.15mm 差。
- **"翼板多分一块"在 {1,2} 包络内不可见**：检查只是 `len(flange_plates) in {1,2}` 且 `sum(quantity)==2`。把一块翼缘错拆成"2 块几何 × 数量 1"仍满足"2 块几何、总数 2"→ 通过。

### 3.2 关键检查是同义反复 / 自指

- **板厚证明是回路**：降低时 web 板厚被直接赋成 `profile.web_thickness`、翼缘赋成 `flange_thickness`（`bh_extractor.py:951-955` 等）；随后"板厚匹配剖面"义务比较的正是这两个量——除非下游篡改，否则永远通过。
- **投影对应证明复用选择判据**：`projection_correspondence_proven = projection_fit OR 独立尺寸证据`，而 `projection_fit` 的阈值正是当初选视图对用的同一残差——"用选择的启发式证明选择"。
- **唯一性在单候选时平凡成立**：`separation_quality=1.0`（有效候选 ≤1 时，`bh_reasoning.py:153-154`）——唯一候选本身可能是错的，但"唯一性证明"照样 PASS。

### 3.3 置信度 90% 来自无法检错的自洽项

`confidence = 0.35·model_fit + 0.35·rule_quality + 0.20·separation + 0.10·evidence_coverage`（`bh_reasoning.py:164-169`）。前三项全部来自内部一致；唯一独立通道 `evidence_coverage` 只占 10%，且"标注稀疏"只记为 warning（`bh_reasoning.py:188-190`）**不阻断**。更关键：**置信度已不授权生产**（`bh_knowledge.py:167-171` 标注 legacy），真正的裁决只看 proof 义务。

### 3.4 阈值系统性偏向"放过"

- web 过深残差打折 `min(delta,2.0)*0.15`（`bh_solver.py:158`），让"错把大块当 web"更容易通过；
- 候选宇宙排除包络 `candidate_universe_residual_limit=0.50`（`bh_knowledge.py:159`）太宽——真正的加劲板/另一构件只要残差>0.50 就被当"非物理几何"放行；
- 孔包含缓冲 `buffer(0.01)`（`bh_validator.py:240`）把中心偏离边界 <0.01mm 的半出界孔判为在材料内。

## 4. 抽取层的具体脆弱点（画法稍有变化即失效）

这是一套**纯手写规则 + 确定性几何，零机器学习**（全仓无 numpy/torch/sklearn，唯一统计函数是 `statistics.median` 用于比例共识）。正确率上限 = "Tekla 导出方言 + 图纸规范"的离散枚举覆盖度。

### 4.1 硬编码外部约定（失效即静默漏抽/硬失败）

| 约定 | 证据 | 失效后果 |
|---|---|---|
| 图层名精确 = `Part/Bolt/PartMark/BoltMark/Z-DIMENSIONS/Section/...` | `bh_dialect.py:109-138`；几何层又硬编码 `=="Part"`/`=="Bolt"`（`bh_geometry.py:123`） | 图层改名/中文化→`UNKNOWN`→实体丢弃 |
| 图层大小写 | 方言用 `casefold`，几何层 `==` 大小写敏感（`bh_geometry.py:123` vs `bh_dialect.py:25-27`） | 同一实体的两套判定打架 |
| 隐藏线线型 = `XKITLINE04`（兼容 `DOT2`） | `bh_knowledge.py:138`、`bh_geometry.py:125-126` | 换线型→隐藏线被当实体边界 |
| `$INSUNITS` 必须已定义（16 种之一，默认 mm） | `bh_canonical.py:22-44`、`bh_regions.py:401-403` | `$INSUNITS=0`（DWG→DXF 常见）→**硬失败** |
| 构件水平放置（longitudinal=X） | `horizontal_axis_fact=True`、`bh_frames.py:502-512` | 竖放/斜放→被拒或取错坐标系 |
| 材料表文本 = `BH H×B×tw×tf` | 正则 `_H_RE`（`bh_semantics.py:15-23`） | 文本变体→元数据解析失败，残差匹配失去基准 |
| 每个视图必须是独立 INSERT 块 | `bh_regions.py:247-263`、`bh_solver.py:201-202` | 全炸开/多视图同块→退化为按 y 带合并，易错并 |

### 4.2 已知的具体缺陷（bug 级）

1. **LWPOLYLINE/POLYLINE 轮廓被声明却未参与 polygonize**：方言层接受（`bh_dialect.py:116`），但 `part_blocks_from_ir` 硬过滤 `dxftype() not in {"LINE","ARC"}`（`bh_semantics.py:341`），`solid_part_entities` 也只收 LINE/ARC（`bh_geometry.py:120-133`）。**Tekla 把板轮廓导出成闭合 LWPOLYLINE 时会被静默丢弃**。
2. **0.985 矩形化**（`bh_geometry.py:531`）：填充率 ≥98.5% 就强制重建精确矩形——**把真实端部斜切/坡口/台阶抹平**的最大元凶。
3. **0.90 腹板净高裁剪**（`bh_geometry.py:1951`）：真实台阶腹板被"等高"假设误裁或误保。
4. **绝对 mm 门限不随尺度缩放**：`100/250/150/120/50/30`（`bh_geometry.py`、`bh_extractor.py` 多处），大构件/小构件共用一套绝对值。
5. **浮点精确相等**：`1e-6/1e-9` 判定几何一致性（`bh_validator.py:71,99,392,402`），对数值噪声敏感。

### 4.3 大量"修复"启发式（改形但不降级）

`ProjectionEdgeAuthority ∈ {DIRECT, PROJECTION_OVERLAY, INFERRED}`（`bh_projection_semantics.py:12-22`）——边界可以是 **INFERRED（猜的）**，但只要带 provenance、几何自洽，仍可 auto_accept。`web_hidden_bridge`（隐藏线桥接）、`flange_rigid_extension`（刚性延长）、`arc_chain_recovery`（圆弧贴回，容差 `max(1.0,8*tol)` 等）、`_regularize_micro_topology`（epsilon=0.25mm 形态学开闭）——这些都是"把不闭合/缺口的几何补成自洽"的操作，补错方向不产生任何证伪信号。

## 5. 方法学定位与根本局限

**学名**：确定性、基于知识/规则的符号推理引擎（rule-based expert system）+ 穷举 generate-and-test + 加权打分，外挂"证明义务 + 溯源台账"审计层。

**不是**：CSP 约束求解器（`bh_constraints.py` 从不"解"约束，只对已构造装配做布尔断言+软打分）；也不是符号演绎定理证明器（"证明义务"只是带 source_id 的断言清单 + 状态机裁决）。

**核心悖论**：它的"证明"绝大多数是对**同一输入派生量**的一致性复检，真正的独立锚点只有三个——剖面文本、显式尺寸标注、图层名。所以它能 fail-closed（缺证据/冲突即拒/转人工，这是它的优点），却**无法发现"错但自洽"的解释**。

| 维度 | 本系统（符号规则） | 几何深度学习/GNN 语义分割 |
|---|---|---|
| 知识来源 | 手写剖面正则、图层映射、阈值、本体常量 | 从标注数据学习 |
| 泛化 | 只在 Tekla 焊接 BH 单件图契约内；换方言即失效 | 可泛化未见风格（对数据分布敏感） |
| 对噪声/缺失图层 | 极脆弱（图层名一变就是 UNKNOWN） | 相对鲁棒 |
| 不确定性 | 伪"置信度"（加权线性组合，非校准概率） | 可输出校准逐图元概率 |
| 可解释/审计 | 强（每结论带 source_id/证据链） | 弱 |
| 安全保证 | 强 fail-closed | 无硬保证 |
| 能否发现"错但自洽" | **不能** | 部分能（外部数据先验打破自洽） |

**一句话根因**：错误发生在**角色指派（感知）这一步**——哪块几何是腹板/翼缘/加劲板、翼缘拆几块——而这一步恰恰是整套系统最薄弱、又被后续"证明"完全掩盖的环节。理想方向是互补：学习模型做开放的感知与角色识别，符号规则做可解释的硬约束与审计，而不是用自洽推理去覆盖感知层的不确定性。

---

## 6. 实证确认（真实样本跑通）

用 `Stages/steel_dxf_split_v1.5.2/.venv`（Python 3.13.13，依赖已装齐）对 `BH_拆板前_dxf汇总` 的 10 个样本 + 单样本 `2b1-cb-40` 完整报告实测：

- **10/10 全部 auto_accepted**（`exit=0`），无 manual_review、无 failed。
- 单样本 `2b1-cb-40` 完整报告关键字段：
  - `automation_route: auto_accepted`
  - `capabilities.ground_truth_used_for_decision: false`（**真值不参与决策，白纸黑字**）
  - `supervised_comparison: null`、`supervised_comparison_used_for_decision: false`（**人工拆板比对为空置**）
  - `confidence: 0.9449`，拆解为 `projection_model_fit 0.35 + semantic_rule_quality 0.32 + hypothesis_separation 0.20 + independent_evidence_coverage 0.075`——**独立证据只贡献 7.5%**
  - 32 条 critical proof obligations **全部 PASS**（TEKLA_CONTRACT / RELEASE_PROFILE / DECOMPOSITION / PLATE_THICKNESS / CONTOUR_TOPOLOGY / SOURCE_EDGE_CONSERVATION / DIMENSION_AGREEMENT / PROJECTION_CORRESPONDENCE / PROVENANCE.FEATURES / UNIQUE_MANUFACTURING_RESULT ……）

这组数据把第 2~3 节的结论从"代码推断"变成"运行事实"：**一个 0.94 置信度、32/32 证明全过的结果，其正确性仍然只由内部自洽保证，运行时没有任何一条证据指向真实拆板结果。**

## 附：最值得优先处理的 5 个具体缺陷点（按影响排序）

1. `bh_semantics.py:341` + `bh_geometry.py:123` —— LWPOLYLINE/POLYLINE 轮廓声明却未消费，统一"语义层接受"与"几何层消费"的实体类型集合。
2. `bh_geometry.py:531`（0.985 矩形化）与 `bh_geometry.py:1951`（0.90 裁剪）—— 两个"改形"阈值是端部斜切/台阶被抹平的直接来源。
3. `bh_canonical.py:43-44` + `bh_regions.py:401-403` —— `$INSUNITS=0` 直接硬失败，应提供"按 bbox 与标注反推单位"的兜底。
4. `bh_geometry.py:123` / `bh_extractor.py:372` —— 图层比较大小写敏感，与方言 `casefold` 不一致。
5. `bh_frames.py:502-512` —— `horizontal_axis_fact` 硬编码，竖放构件必失败。
