# BOX CAD Agent 只读迁移审计与 DXF 迁移矩阵

## 1. 审计结论

本文件只把 CAD Agent BOX 规则视为待验证的领域假设，不把 DWG、AutoLISP、CAD 句柄、CAD bbox、活动文档或 WBLOCK 行为迁入 DXF 实现。

迁移后的 BOX 路径应遵守以下边界：

- 事实由 `ezdxf` 直接读取，并保留块层级、实例变换、原语、文字、单位和来源关系。
- 几何由 `ezdxf` 原语与 Shapely 拓扑原生重建；bbox 只能用于索引和预筛，不能作为异形板、孔归属或几何等价的最终证明。
- 文字证据与几何证据隔离。零件编号只能证明身份，数字尺寸只能校验已有几何，任何文字都不能创建或补全板件候选。
- 候选必须形成完整四板装配体后统一求解。硬约束决定是否可接受，软证据只能对已经通过硬约束的完整解排序。
- 角色不能由 CAD 图块窗口或图纸绝对位置直接绑定。只有在 DXF 中先证明了视图坐标系、截面语义和四板对应关系后，位置才可以作为受限软证据。
- 事实不完整、几何不闭合、孔归属不唯一、同源证据冲突、四板多解、分差不足或输出验证不完整时，一律 fail closed。
- 最终真值是 20 组 DXF 拆板前后监督样例。CAD Agent fixture 中的坐标、图层名、数量、阈值和单张 DWG 结论都不是 DXF 通用规则。

## 2. 读取时的版本边界

读取前已检查两个仓库状态。

### 2.1 CAD Agent

- 仓库：`D:\Dev\Projects\cad Agent`
- 当前分支：`codex/feature/20260717/build-box-finite-domain-constraint-solver`
- 当前 HEAD：`d963a860d86b14c0ad77a047f7d2d8f6183dd504`
- HEAD 提交：`d963a86 fix: harden CAD execution and archive completed specs`

以下核心文件在初始状态快照中只存在于 working tree，未进入 HEAD：

- `app/workflows/box_facts.py`
- `app/workflows/box_text_evidence.py`
- `app/workflows/box_geometry_ir.py`
- `app/workflows/box_solver.py`
- `tests/test_box_facts.py`
- `tests/test_box_text_evidence.py`
- `tests/test_box_geometry_ir.py`
- `tests/test_box_solver.py`
- 本轮有限域 solver 的 spec、plan 和 OpenSpec change

最终 live 状态复核仍为同一 HEAD，但 working tree 已有 59 个 porcelain 项，说明
CAD Agent 在审计期间继续发生外部并发演进。除前述 facts/IR/solver 外，新增内容已经
扩展到：

- boundary probe/runner；
- projection profile 和 source graph；
- solution export 及其 LISP builder/runner；
- 相应 fixture、测试、plan、spec 和 OpenSpec change；
- `executor.py`、`finished_quality.py`、`combined_output.py` 等 CAD 集成文件。

这些内容仍未进入 HEAD，本次也没有在只读参考仓库中运行测试或生成产物。因此它们
仍属于“未提交、未完成 DXF 真值验证的演进假设”，不能反向覆盖 DXF 前后样例证据。

初始与终检两个快照共同证明 working tree 正在变化，不能把其中任一中间状态描述为稳定基线。只有 HEAD 内容可称为已提交基线；所有新增 BOX finite-domain 文件都必须继续与最终 git 状态、测试结果和真实样例证据对照。

以下文件属于 HEAD 已提交基线，可用于理解已存在的流程和质量门，但仍不能直接证明 DXF 规则：

- `box_geometry.py`
- `box_profile_policy.py`
- `box_main_path.py`
- `box_primitives.py`
- `box_lisp_builder.py`
- `finished_quality.py`
- `combined_output.py`
- 对应的已提交测试

本次审计未修改 CAD Agent，也未在该仓库运行会生成缓存或产物的命令。

### 2.2 DXF 项目

- 独立实现 worktree：`D:\Dev\Projects\dxf agent\worktrees\box-completion`
- 当前分支：`codex/feature/box-instance-reconstruction-active`
- 基线 HEAD：`9cee27cc25d62010f5971cfd420576d46845a8ed`

原工作区 `D:\Dev\Projects\dxf agent\steel_dxf_split_v1.1.0 - 副本` 仍停在同一基线
提交，保留用户的 README 修改、5 个 BH 样例删除及未跟踪目录；本 worktree 没有修改或
回滚这些既有内容。

独立 worktree 在既有 BOX facts、metadata、compiler 和 pipeline 基础上新增或重写：

- `box_projection.py` 的开放投影闭环、站位轨道、端链和截面投影；
- `box_geometry_ir.py` 的内禀尺度容差和大图空间索引；
- `box_view_ir.py` 的坐标轴符号规范化；
- `box_reconstruction.py` 的直接轮廓、多视图重建、外表面、端链、developed rails
  和实例级候选；
- `box_solver.py` 的双轨道四角色组合、完整主证据、回退比例、最小闭环和多解门；
- `box_manufacturing.py` 的制造语义规范化指纹；
- `box_supervision.py` 和 `box_corpus_audit.py` 的逐板最优匹配与结构/连续几何分离。

权威 BOX 监督数据位于 `D:\DevData\BOX拆板前后数据`。2026-07-18 修正扫描范围后确认：

- `BOX_拆板前_dxf` 与 `BOX_拆板后_dxf` 各 20 个文件；
- 20 个 pair 全部完整，20 个源图全部识别为 BOX；
- 缺边、不可读和非 BOX pair 均为 0；
- 已生成包含全部 40 个文件 SHA-256 的 10/10 分区 manifest 草案。

本次运行使用该数据的只读项目镜像
`D:\Dev\Projects\dxf agent\.codex-golden\BOX拆板前后数据`。原 v3 REGION
审计已因 `2b2-cb-145`、`2b2-cb-155` 各漏一张成品板而作废；修复后重新验证结果为：

- 20/20 solver `pass`；
- 四角色制造证明共 80 个实例，去重后的真实成品共 63 张；
- 20/20 成品板数、标签数、孔数、孔归属和唯一解等结构门通过；
- `2b2-cb-145`、`2b2-cb-155` 均按 3 个 REGION、3 个标签和 3 张生成板比较；
- 20/20 满足当前连续几何比较阈值；
- `h-4-cb-38` 经跨投影区域坐标轴换算修复后，最大边界差为 0.877723 mm，
  最大孔心差为 0.000092 mm；
- 旧 v2/v3 机器报告仅保留为缺陷证据，不得再授权验收。

## 3. 当前 DXF BOX 路径的主要缺口

| 当前实现 | 已有价值 | 对 BOX 新路径的风险 | 迁移要求 |
|---|---|---|---|
| `load_document()` | 使用 `ezdxf` 读取、恢复和 audit | 尚未形成有上限、可指纹化的完整 Source Facts | 保留读取入口，增加事实完整性、大小限制和源文件指纹 |
| `recursive_virtual_entities()` | 能展开嵌套 INSERT 并应用实例变换 | 展平后来源树和定义级来源容易丢失 | 保留几何展开能力，同时记录块祖先链和定义原语来源 |
| `collect_instances()` / `BlockInstance` | 收集块实例、图层计数和文字 | 仍以句柄和块名为主要来源标识 | 句柄只允许诊断；建立不依赖句柄的确定性 `source_key` |
| `_candidate_views()` | 从实体重建闭环并结合名义尺寸筛选 | 固定要求 `Part` 图层，未知/0 层几何直接丢失 | 图层降级为证据，不作为唯一资格门 |
| `_select_primary_view_pair()` | 枚举视图对并检查尺寸 | 依赖图纸空间距离与左到右排序 | 重写为完整候选组合求解；位置只能在视图框架已证明后使用 |
| `_choose_orientation()` | 枚举方向和厚度组合，用重量做一致性检查 | 最小误差方案可能掩盖多解；1% 和 0.01% 为现有样例阈值 | 重写为硬约束加安全分差；阈值由 20 组样例标定 |
| `_assembly_number()` | 能从表格或文件名获得编号 | `@` 文件名回退违反来源身份 fail-closed 原则 | 禁止文件名授权最终编号；编号必须来自源 DXF 唯一文字证据 |
| `_stud_set_candidates()` | 实体顺序无关的符号中心提取 | 固定 `Bolt` 图层，并用 bbox 判断属于某视图 | 图层仅作证据；最终用 Shapely 拓扑归属 |
| `models.py` 的 `Contour` / `Plate` | 有基本板件模型 | 不保存每条边、孔、文字和块的来源链 | 为 BOX 新增独立 Source/Geometry/Candidate/Solution/Manufacturing IR |
| `bh_frontend.py` / `bh_fingerprint.py` | 已有 SourceRef、Fact IR 和 canonical SHA-256 模式 | BH 的字段与语义不能直接冒充 BOX | 复用模式和通用工具，不复用 BH 领域结论 |
| `bh_geometry.py` / `bh_validator.py` | 已使用 Shapely polygonize、Polygon 和输出验证 | 容差和 BH 特有修复不可直接移植 | 复用拓扑工具思想，BOX 容差和门禁重新标定 |

## 4. 推荐的 DXF 证据分层

建议 BOX 新路径与现有旧 BOX extractor 并存一段时间，先完成监督回放，再决定替换入口。推荐职责边界如下：

1. `box_source.py`：读取 DXF、建立有上限的 immutable facts、来源树和 source fingerprint。
2. `box_text_evidence.py`：只分类和分配文字权限，不创建几何候选。
3. `box_geometry_ir.py`：把 ezdxf 原语转换为世界坐标曲线和 Shapely 拓扑，产生外轮廓、内轮廓、孔和几何签名。
4. `box_candidates.py`：构建候选区域、记录聚类/轮廓来源、完成同源归一化与冲突拒绝。
5. `box_solver.py`：枚举完整四板解，执行硬约束、软证据排序、唯一解和安全分差判断。
6. `box_contracts.py`：把唯一 solution 降低为制造 IR，并执行四板批次原子门。
7. `box_writer.py` / `box_validator.py`：写 DXF、重新打开、比较逐板拓扑签名、标签、孔和污染实体。

`source_key` 不得以 CAD/DXF handle 为身份基础。建议由以下确定性信息构成：

- 源文档 canonical fingerprint；
- 空间与块祖先链的 canonical 内容签名；
- 实例变换的规范化表示；
- 原语规范化几何签名；
- 完全相同实例在 canonical 排序后的 occurrence index。

DXF handle 可以保留在 diagnostics 中帮助人工追踪，但不得参与候选资格、去重、角色、签名或最终标签判断。

## 5. 迁移矩阵

| CAD Agent 规则/函数 | 解决的问题 | DWG 依赖 | DXF 对应证据 | 处理决定 |
|---|---|---|---|---|
| `BoxSourceFactsV1` | 把源文件身份、单位、扫描完整性、块实例、原语和文字冻结为事实 | 低 | DXF 文件 SHA-256、`$INSUNITS`、modelspace、block records、INSERT 祖先链、原语与文字事实 | 保留；用 ezdxf 重写数据采集 |
| `BoxTextFact` | 保存文字原文、位置、块来源和分类 | 低 | TEXT、MTEXT、ATTRIB、ATTDEF 的原文、规范化文本、世界坐标、块祖先链 | 保留；扩充文字类型和来源树 |
| `BoxPrimitiveFact` | 保存受支持曲线原语及图层、线型和来源 | 低 | LINE、ARC、CIRCLE、LWPOLYLINE 的 DXF 属性、bulge、图层、线型和实例变换 | 保留；禁止使用 CAD bbox/handle 作为事实主体；其他曲线 fail closed |
| HATCH 边界事实 | 防止填充边界被层名过滤后静默丢失 | 低 | 每条 HATCH boundary path 的有限展平点、闭合状态、来源作用域和确定性 fingerprint | 重写；仅在与候选视图分离或被同作用域角色视图物理线完整覆盖时视为辅助 |
| `BoxBlockInstanceFact` | 保留定义级几何与实例变换，区分同定义的多个实例 | 低 | ezdxf INSERT、block definition、嵌套祖先链、平移/旋转/缩放矩阵 | 重写；支持嵌套块并记录完整仿射变换 |
| `source_key` | 在 facts、候选、拒绝原因和最终证明之间建立来源链 | 高 | 文档指纹、canonical 块路径、实例变换、规范化几何签名、重复 occurrence index | 重写；DXF handle 仅作诊断，不能作身份 |
| `parse_box_source_facts()` | 拒绝 schema 错误、缺字段、非有限数、截断和计数不一致 | 无 | ezdxf 读取结果、DXF audit、实体计数、显式 schema、有限数检查 | 保留；采用 DXF 原生事实 schema |
| facts 数量、字节、深度和字符串上限 | 防止无限输入、异常嵌套和不可审计的大 payload | 无 | 文件大小、实体数、块深度、文本长度、曲线点数、展开预算 | 保留；上限需按 DXF 语料与运行预算设定 |
| facts immutable/frozen | 防止分类或求解阶段静默改写源事实 | 无 | frozen dataclass、tuple、只读 mapping，派生对象返回新 fingerprint | 保留 |
| `box_facts_fingerprint()` | 对同一观察事实产生顺序无关、可复放的 SHA-256 | 无 | canonical JSON；稳定排序后的块、实体、文字、变换和单位 | 保留；可复用 `bh_fingerprint.py` 的 canonical 模式 |
| `classify_box_text_fact()` | 区分零件编号、数字尺寸、截面/比例、图签和未知文字 | 低 | DXF TEXT/MTEXT/ATTRIB/ATTDEF、信息表区域、唯一编号解析结果 | 保留规则框架；分类语义用 20 组样例验证 |
| `text_permission()` | 限制不同文字类别能参与的推理通道 | 无 | `identity_only`、`geometry_check_only`、`excluded`、`unknown` 权限字段 | 保留 |
| `geometry_authorized_source_keys()` 返回空 | 明确文字永远不能凭自身授权几何候选 | 无 | 只有闭合拓扑和来源完整性才能产生候选 | 保留为硬不变量 |
| 未知文字拒绝自动 final | 防止未识别注释被误当成尺寸或零件证据 | 低 | unknown text 与其块/区域来源、独立排除证明 | 保留；允许“独立证明该文字属于被排除图签区域”，不能静默忽略 |
| `build_box_geometry_ir()` | 把完整 facts 转换为无角色的世界坐标几何候选 | 低 | ezdxf 虚拟实体/矩阵变换、Shapely Polygon/MultiPolygon、原始曲线证据 | 重写；不依赖 CAD 几何 API |
| `_transform_primitive()` | 应用块基点、插入点、缩放和旋转，保留 ARC/CIRCLE/bulge | 低 | ezdxf Matrix44 或等价二维仿射；原始 bulge 和圆弧参数 | 保留数学目的；用 ezdxf 原生实现 |
| 非均匀缩放拒绝 | 避免圆弧/圆在不可精确表示的变换后被伪装为原几何 | 低 | 仿射矩阵、曲线类型、是否可精确降低为 ellipse/spline | 保留 fail-closed；如未来支持 ELLIPSE，必须显式扩展 schema 与测试 |
| `_reject_duplicate_curves()` | 防止重复必要边把拓扑伪装成闭环 | 无 | 方向无关的规范化曲线签名、source provenance | 保留 |
| `_connected_cycles()` / `_trace_cycle()` | 从端点邻接恢复闭环并识别悬空/分叉组件 | 低 | Shapely `unary_union` 后的 noded linework、`polygonize_full` 的 polygons/dangles/cuts/invalids | 重写；优先用 Shapely 拓扑诊断 |
| `_select_outer_and_inner_loops()` | 证明唯一外轮廓、内轮廓位于外轮廓内且互不冲突 | 无 | Polygon shell/interiors、`is_valid`、`covers`、`contains`、`intersects`、DE-9IM | 重写；bbox 只预筛 |
| 自交、外内环相交、内环互交或嵌套拒绝 | 阻止不可制造或拓扑含义不唯一的板件 | 无 | `is_valid`、`explain_validity`、边界 intersection、环层级 | 保留；对合法多层岛结构另立明确支持域，不能默认接受 |
| `geometry_signature` | 让原语顺序变化不改变候选身份，同时保留真实曲线语义 | 无 | 平移归一化后的 Polygon WKB/WKT、规范化 bulge/arc、孔排序和精度模型 | 保留；签名必须包含孔和曲线类型，不能只签 bbox |
| `BoxPlateCandidate` | 汇总外轮廓、内轮廓、bbox、质心、长轴、尺寸、来源和数字尺寸 | 无 | Shapely polygon、representative point/centroid、minimum rotated rectangle、曲线与文字 provenance | 保留字段目的；bbox 和轴向只是派生属性 |
| `select_adaptive_thresholds()` | 根据图纸尺度选择端点吸附、聚类和歧义阈值 | 低 | `$INSUNITS`、已证明的绘图比例、端点残差分布、几何尺寸统计、监督误差 | 重写；禁止复制 `0.5`、`0.001`、`0.03` |
| `build_v3_candidate_regions()` | 汇合阈值、端点连通、候选排序、第四/第五歧义、可疑几何和孔归属门 | 低 | DXF 原语图、Shapely polygonize 结果、候选 provenance、歧义与 ownership diagnostics | 重写；拆成候选构建与门禁，不保留 bbox/固定阈值实现 |
| `build_endpoint_groups()` | 把容差内端点归并，恢复被微小绘图误差打断的轮廓 | 低 | STRtree/邻域查询、端点距离、noding 前后差异、修复记录 | 重写；每次吸附必须记录位移且受最大修复预算约束 |
| `_connected_components()` | 从原语连通关系形成候选区域 | 低 | noded line graph、Shapely connected components、polygonize 结果 | 重写；不能以 bbox touching 作为最终连通 |
| `_meaningful_cluster()` | 排除过小、退化或明显辅助的区域 | 低 | Polygon 面积、周长、有效性、闭环数、源实体类型、监督分布 | 软证据；固定实体数/面积/宽高阈值禁止迁移 |
| `_rank_clusters()` | 对多个候选提供确定性排序 | 低 | 拓扑完整性、来源质量、几何稳定性、尺寸一致性和 canonical tie-break | 重写为候选证据排序；排序不能直接授权 final |
| `_ambiguous_boundary()` | 第四、第五候选接近时拒绝冒险选前四 | 无 | 第四/第五完整解的硬约束状态、校准后的证据分差和候选敏感性 | 保留歧义拒绝目的；阈值由监督样例标定 |
| 可疑图层/线型过滤 | 降低中心线、隐藏线、尺寸线和辅助线污染 | 低 | layer、linetype、entity type、颜色以及其是否参与闭环 | 软证据；名称不能成为唯一拒绝或接受门 |
| `assign_hole_ownership()` | 要求每个孔只属于一个板件 | 低 | 孔 Polygon/Circle 与候选 Polygon 的 `covers/contains/intersection`、边界距离 | 重写；bbox 只预筛，唯一拓扑归属才可通过 |
| `_bbox_contains()` / `_bbox_overlaps()` | 快速筛选候选与孔的可能关系 | 高 | Shapely STRtree 或 bounds 查询 | 仅保留为预筛；禁止作为最终几何或孔归属证明 |
| `select_profile_pair_windows()` | 从截面、marker 和视图候选中寻找 2 web + 2 flange 的唯一组合 | 高 | DXF 截面文字、已证明的截面轮廓、尺寸图、块来源、视图关系图 | 重写为软证据适配器；不得独立绑定角色 |
| marker group 唯一性和 strong anchor | 要求截面/编号锚点与候选组合一致且无冲突 | 高 | 文字到几何区域的 Shapely 距离/覆盖、共同来源块、截面标记关系 | 软证据；文字锚点不能授权几何 |
| profile quartet score margin | 最优视图组合与次优组合过近时拒绝 | 高 | 完整候选组合的校准分差、证据来源数量、约束残差 | 保留歧义门；禁止复制 `5.0`、`3`、`0.05` 等值 |
| `solve_box_finite_domain()` | 在有限候选域内枚举所有四元组并寻找唯一合法解 | 无 | `itertools.combinations`、无角色 `BoxPlateCandidate`、结构化 constraint report | 保留核心方法；在 DXF 中原生实现 |
| 四个不同 `source_key` | 防止同一物理来源重复充当两块板 | 无 | canonical source provenance 与 geometry signature | 保留硬约束 |
| `geometry_complete` | 保证每板有有限、非退化、闭合且可追踪的几何 | 无 | Shapely valid polygon、正面积、完整 shell/holes、来源覆盖率 | 保留硬约束 |
| `physical_non_overlap` | 防止四块候选实际指向重叠物理区域 | 低 | 原始视图域中的 Polygon intersection area、来源冲突、装配视图关系 | 重写；不能使用 bbox overlap 作为最终判断 |
| `orientation_count` 的 2 vertical + 2 horizontal | 用图纸方向构造两对板件 | 高 | minimum rotated rectangle、截面/投影视图证据、板件成对尺寸 | 软证据或受限硬约束；必须由 20 组样例证明适用域 |
| `_paired_geometry_consistent()` | 要求两块腹板和两块翼板各自形成一致的几何对 | 低 | 平移/镜像归一化后的 Polygon 等价、尺寸/面积/孔模式残差 | 保留领域目的；用拓扑签名而非宽高 alone |
| `_numeric_dimensions_consistent()` | 数字尺寸只检查已有几何是否冲突 | 无 | 已分类 numeric text、标注关联图、几何测量值 | 保留为硬冲突门或软一致性证据；文字不能覆盖几何 |
| `_candidate_score()` | 用图层和数字尺寸给合法候选排序 | 低 | 来源质量、图层、尺寸一致性、修复成本、监督校准权重 | 重写为软证据；分数不得覆盖硬约束 |
| safe margin / 多解拒绝 | 最优解与次优解相同或过近时返回人工复核 | 无 | 完整解排序、绝对/相对分差、校准置信区间 | 保留；`0.25` 禁止直接复制 |
| `_bind_side_roles()` 按世界 X/Y 绑定左右上下 | 从图纸空间位置直接生成业务角色 | 高 | 只有已证明的局部截面坐标系、视图对应和前后样例角色映射 | 禁止直接迁移；未证明局部坐标系时不得绑定角色 |
| `box_solution_business_roles()` | 把唯一几何角色映射到固定业务角色和板号 | 低 | 已证明的 solver solution、角色契约、监督样例对应关系 | 保留映射层；映射前提必须在 DXF 中重新证明 |
| `BoxCandidateEvidence` / `normalize_box_candidate_evidence()` | 汇合 marker、window、profile、primitive 等证据，去重并在同源冲突或角色预绑定时拒绝 | 低 | 统一 candidate evidence、source key、geometry signature、adapter provenance | 保留；DXF 中只允许一个最终 solver，当前 CAD 实现仍是未提交草稿 |
| `select_box_plate_candidates()` | 统一候选发现入口并在 v3 硬拒绝时阻止 legacy 回退绕门 | 高 | DXF topology candidates、拒绝码、适配器证据 | 重写；禁止 AutoLISP、全局 0 层选择和 legacy 旁路 |
| `build_box_main_path_diagnostics()` | 串联 facts、候选、gate、manual review 和证据摘要 | 低 | DXF pipeline stage result、candidate/solution/contract diagnostics | 保留流程组织；不保留 CAD stage 和 WBLOCK 字段 |
| `_run_box_unified_solver_pipeline()` | 固定 facts 提取、解析、分类、几何、归一化和单次求解的调用顺序 | 高 | DXF 文件读取、Source Facts、text permissions、Geometry IR、candidate normalization、solver result | 重写为纯 Python DXF pipeline；禁止 LISP probe，当前 CAD 接入仍未提交 |
| diagnostics 稳定 reason code | 让事实、拓扑、候选、求解和输出失败可审计 | 无 | 枚举型 reason code、结构化 evidence refs、有限摘要 | 保留；原始大 facts 不进入公共结果 |
| `build_box_source_fact_probe_lisp()` | 从活动 DWG 只读扫描 ModelSpace、INSERT 和块定义 | 高 | ezdxf 直接读文件、blocks、layouts、INSERT、attribs、header | 禁止迁移 AutoLISP/COM；用 ezdxf 重写事实入口 |
| `build_box_primitive_facts_lisp()` | 选择 CAD Part 图层原语并导出候选事实 | 高 | ezdxf entity query、语义图层分类、完整块来源 | 禁止迁移 LISP/ssget/VLA；图层只能作证据 |
| `build_box_primitive_wblock_lisp()` | 按候选 bbox/选择集生成独立 DWG | 高 | DXF Manufacturing IR、ezdxf writer、明确实体复制清单 | 禁止迁移 WBLOCK；由 DXF writer 从已证明几何重建 |
| `box_lisp_builder.py` 的 INSERT inventory/window/bbox | 发现 CAD 运行时隐藏的块窗口和空间假设 | 高 | DXF BlockIR、transform、Polygon、视图关系图 | 仅作为假设来源；禁止移植实现 |
| `resolve_box_plate_roles()` 的宽度/面积/高度分组 | 在四板候选中识别两类成对板件 | 低 | Polygon 等价、截面尺寸、厚度、孔模式、质量守恒 | 软证据；固定 3%/15%/20%/25%/35% 阈值禁止迁移 |
| `finished_quality` 的轮廓/孔/污染门 | 判断候选能否升级为最终板件 | 低 | 保存后重新读取 DXF、Shapely validity、逐板 topology fingerprint、实体白名单、标签检查 | 保留质量门目的；重写为 DXF validator |
| strict source/output signature proof | 防止写出过程改变已证明几何 | 低 | Source Fact fingerprint、Manufacturing IR fingerprint、saved DXF fingerprint | 保留；不使用 LISP 或 DWG reopen proof |
| forbidden/unknown entity checks | 防止尺寸、图签、辅助线、未知实体污染成品 | 低 | 保存后 DXF modelspace 实体清单、图层和语义角色 | 保留；允许列表必须由输出 profile 明确给出 |
| `combined_finished_plates_v1` 四板全证明门 | 任一板不完整时阻止整批 final | 低 | 四个独立 plate proof、唯一 solution、batch contract report | 保留批次原子性；不要求迁移 DWG 文件提升逻辑 |
| `plan_combined_plate_layout()` translation-only | 避免输出布局改变板件尺寸与拓扑 | 低 | Shapely affine translate、逐板前后 signature | 保留 translation-only 原则；DXF 输出布局按本项目契约定义 |
| `validate_combined_delivery()` | 验证四板顺序、标签、逐板签名、污染和重新打开证据 | 低 | DXF writer 输出、重新读取的四板 Polygon/holes/text、contract report | 重写；不迁移 DWG 路径、文件移动和 promotion |
| `test_box_facts.py` | 证明完整性、上限、稳定指纹、不可变和 fail-closed | 无 | DXF facts 单元 fixture | 保留测试意图；fixture 数值不是规则 |
| `test_box_text_evidence.py` | 证明分类权限狭窄且文字不授权几何 | 无 | DXF text facts 与权限矩阵 | 保留测试意图 |
| `test_box_geometry_ir.py` | 证明变换、曲线、孔、签名和拓扑拒绝 | 低 | ezdxf 原语 fixture + Shapely 拓扑断言 | 保留测试意图；增加嵌套块、ELLIPSE/SPLINE 和单位用例 |
| `test_box_geometry.py` / `test_box_primitives.py` | 证明第四/第五候选、可疑几何和孔归属会拒绝 | 低 | DXF 候选图、Shapely ownership、歧义诊断 | 保留反例；禁止复制矩形坐标、实体数和阈值 |
| `test_box_solver.py` | 证明候选不足、多解、重叠、来源复用、方向冲突和分差不足会拒绝 | 无 | DXF candidate fixture 与完整 quartet 枚举 | 保留测试意图；重写空间角色用例 |
| `test_box_profile_policy.py` / replay | 证明 marker/profile 组合必须唯一且有安全分差 | 高 | 20 组 DXF 的截面、文字、块和拓扑回放 | 软证据；必须用 20 组重新验证 |
| `test_finished_quality.py` / `test_combined_output.py` | 证明逐板质量和批次输出均通过后才允许 final | 低 | saved DXF reopen、逐板 fingerprint、实体污染与标签检查 | 保留质量门意图；禁止 DWG promotion/WBLOCK 假设 |
| 2026-07-17 solver spec/plan/OpenSpec | 描述统一 facts、IR、adapter、solver 和输出链的目标架构 | 高 | 只能与当前代码、DXF 监督回放和实现测试相互印证 | 仅作未提交设计假设；不得视为已验证结论 |

## 6. 20 组 DXF 监督验收门

当前状态：**算法结构验收 20/20，生产授权未冻结**。实现报告仍设置
`supervised_sample_gate_unverified`、`production_ready=false` 和
`review_required=true`。若调用方显式要求 `require_auto_accept`，BOX 主路径会在写出前
拒绝。连续几何比较为 20/20；生产授权仍只取决于监督 manifest 和 proof 是否人工冻结。

在冻结 production proof 前，应先冻结样例 manifest，至少记录：

- 拆板前、拆板后文件的路径、SHA-256、DXF 版本、`$INSUNITS` 和 audit 结果；
- 每组期望的四板角色、外轮廓、内轮廓、圆孔/异形孔、厚度、标签和数量；
- 每个真实板件从拆板前图到拆板后图的人工对应关系；
- 样例是否包含嵌套块、0 层几何、非均匀缩放、圆弧/bulge、辅助线、第五候选或重叠视图。

建议把 20 组预先分成“容差标定集”和“冻结验收集”，避免在全部样例上反复调参后再声称泛化通过。若样例数不足以稳定分组，则至少保留逐组 leave-one-out 结果，并记录每个阈值由哪些样例决定。

每组必须同时通过：

1. 同一输入重复解析得到相同 Source Facts fingerprint。
2. 实体顺序、块定义枚举顺序变化不改变候选和 solution。
3. 四块板均有唯一外轮廓，孔和内轮廓均唯一归属。
4. 生成板件与人工拆板后板件在平移归一化后拓扑等价。
5. 逐板角色与人工真值一致，不依赖文件名、handle 或绝对图纸坐标。
6. 第四/第五候选接近、同源冲突、孔跨板、轮廓断裂或多解反例返回 manual review。
7. 写出后重新打开，四板的几何、孔、标签和单位仍与 Manufacturing IR 一致。
8. 任一板未证明时，不产生 final 批次。

推荐的几何验收指标应同时包含：

- 对称差面积；
- Hausdorff 距离或边界最大偏差；
- 外轮廓与内轮廓数量；
- 圆孔中心/半径误差；
- 异形孔 Polygon 等价；
- 面积、周长和 minimum rotated rectangle 尺寸；
- 曲线类型保真度，特别是 ARC、CIRCLE 和 bulge；
- 来源覆盖率与未解释实体数。

具体阈值必须根据 DXF 单位、绘图比例、人工拆板误差和 20 组统计分布标定，不能从 CAD Agent 常量复制。

## 7. 明确禁止迁移

- AutoLISP 字符串、`ssget`、VLA/COM、活动文档、ZWCAD 状态和 WBLOCK。
- CAD/DXF handle 作为稳定身份、去重、候选资格或角色依据。
- CAD bbox contains/overlaps 作为异形板、孔归属或板件重叠的最终判断。
- 固定 `Part`、`Bolt`、0 层或块名作为唯一资格门。
- 以文件名、`@` 后缀、缓存编号或外部编号回退生成最终标签。
- 以图纸世界坐标 X/Y 直接绑定右/左/下/上角色。
- 从 fixture 复制实体数、坐标、面积、距离、比例或 safe-margin 常量。
- 让 profile、marker、primitive 或 legacy adapter 各自产生最终角色证明。
- 为提高通过率而放松未知文字、轮廓完整性、孔唯一归属、多解或批次原子门。

## 8. 可直接复用的 DXF 项目能力

- `ezdxf` 读取、recover 和 audit。
- `recursive_virtual_entities()` 的嵌套块展开能力，但必须补充来源祖先链。
- `bh_ir.py` 的 Fact IR 分层思想和 `SourceRef` 模式，但要去除 handle 身份依赖。
- `bh_fingerprint.py` 的 canonical JSON、顺序无关 contour 和 SHA-256 模式。
- `bh_geometry.py` 的 Shapely polygonize、noding、precision 和 Polygon 操作方式。
- `bh_constraints.py` 的硬规则、软质量和结构化 rule evaluation 模式。
- `bh_solver.py` 的完整假设枚举与拒绝诊断模式。
- `bh_contracts.py` 的分层制造合同和不可跳过质量门。
- `bh_writer.py` / `bh_validator.py` 的确定性写出与重新打开验证模式。

这些能力只能复用工程模式和通用工具。BH 的阈值、角色、视图假设和制造语义不能直接变成 BOX 规则。

## 9. 2026-07-18 DXF 原生实现结果（历史 v1，已被第 10 节更正）

已新增：

- `box_facts.py`：有上限的 `BoxSourceFactsV1`、`BoxPrimitiveFact`、`BoxTextFact`、无 handle 身份的 `source_key` 和 canonical SHA-256；
- `box_text_evidence.py`：零件号、数字尺寸、BOX 截面、比例、图签和其他文字分类，固定 `can_authorize_geometry=false`；
- `box_geometry_ir.py`：按来源作用域做 Shapely noding/polygonize、无 handle 端点图全环恢复、凹多边形拓扑和孔唯一归属；
- `box_candidate_evidence.py`：闭合环候选、内平行线 strip 派生、几何原型归并和成对实例；
- `box_solver.py`：完整四板组合、硬拒绝、质量/尺寸残差、第四/第五候选分差和结构化处置；
- `box_contracts.py` / `box_native.py`：活动源文字身份门、solver 到制造几何一致性、20 组监督风险门；
- `box_supervision.py` / `box_supervision_cli.py`：监督 pair 发现与 BOX 分类、文件
  SHA-256 manifest、10/10 分区、逐板外轮廓/面积/孔比较、逐对 verdict、manifest 和
  实现漂移绑定的 gate proof；
- `box_pipeline.py`：BOX 原生证据合同接入，所有 DXF 与 JSON 先暂存和回读，全部通过后提升，提升失败回滚；`pipeline.py` 只负责 BH/BOX 识别与延迟分派。

真实样例结果：

| 样例 | facts | 几何原型 | solver | 领先差距 | 合同 |
|---|---:|---|---|---:|---|
| `BYSJ-L5-FG-035@L5-FG-35.dxf` | 8868 primitive / 340 text | 1 个 200 mm flange；2 个 518 mm web 候选 | `pass`，质量证据选中 5291.079 × 518 mm | 0.104457 | `hard_pass=true`，`runtime_authorized=true` |
| `BYSJ-B7-B1-GGZ-004@B7-B1-A4-GGZ-1.dxf` | 16772 primitive / 398 text | 700 mm flange；由平行线重建 644 mm web | `pass` | 0.057783 | `hard_pass=true`，`runtime_authorized=true` |

两张项目内回归样例仍保留 `production_ready=false`；原因是外部 20 组数据虽已齐备，
但监督 manifest 仍是探索性草案，尚未完成人工冻结和逐对验收。
机器审计文件 `docs/superpowers/reports/2026-07-18-box-supervised-corpus-discovery.json`
确认外部根目录 20/20 完整且全部为 BOX；草案文件
`docs/superpowers/reports/2026-07-18-box-supervised-manifest.draft.json` 已记录全部文件
SHA-256 和 10/10 分区。只有冻结且人工批准的 manifest 逐对通过后生成的未漂移 proof
才能授权生产门。

新增 40 个 BOX 原生/合同/监督/原子性测试；现有 BOX 6 个回归测试在接入后继续通过，
最终 BOX 全集 46/46。测试覆盖：

- fingerprint 对遍历顺序和 handle 变化不敏感，嵌套 diagnostics 深层不可变；
- 非有限输入和 primitive 上限拒绝；
- 文字不能授权几何；
- 未闭合、退化、凹多边形 bbox 假阳性和孔多归属拒绝；
- 唯一解、多解、无解、来源缺失和质量证据选择；
- 两张真实 BOX 样例的新证据合同；
- 监督 pair 发现、BOX/BH 分类、manifest 哈希和人工冻结门；
- calibration/acceptance 计数、逐板边界/面积/孔心/孔径比较和逐对 verdict；
- manifest、实现代码与 proof 内部状态漂移拒绝，以及 proof 到生产主路径的接线；
- 写出中途失败不留下任何正式半成品；
- 保存后 DXF audit、四个闭合板轮廓、标签、栓钉显示和辅助线清洁度。

全仓完整运行收集 154 个测试，结果为 140 passed、14 failed；其后新增实现指纹覆盖和
镜像前后目录配对测试，监督相关测试 12/12 通过，当前收集数为 156。全部 14 个失败均为当前工作区
5 个已删除 BH 样例导致的 `FileNotFoundError`；没有 BOX 或其他代码断言失败。本轮没有
恢复或修改这些用户已有删除。

## 10. 2026-07-18 v3 权威结果

第 9 节记录的是被替换的第一版“旧 extractor 外包新证据合同”方案，不能再作为当前
实现或验收结论。当前权威架构和结果如下：

- BOX runtime 已删除对 `extract_box_assembly()`、`box_native.py`、
  `box_candidate_evidence.py` 和 `box_contracts.py` 的权威依赖；
- 新路径为 facts → metadata → geometry IR → view IR → multiview
  reconstruction → complete solver → manufacturing IR；
- BH 只保留工程模式借鉴，BOX domain 与监督代码均不导入 `bh_*`；
- 拆板后 ACIS REGION 由中性 `reference_geometry.py` 离线解析，不进入生产推理；
- 新增纯 DXF 投影闭环、站位轨道、端链、developed rails 和实例级四板重建，不要求
  原图先存在四块闭合板轮廓；
- 真实样例的整体旋转、平移、实体倒序和 handle/path 变化不改变制造语义指纹；
- 正式 pipeline 的 BH 风格直接输出仍必须通过内部结构、几何和保存后回读门；
  显式使用 `require_auto_accept` 时还必须提供与当前实现和 manifest 匹配的 proof；
- 20/20 solver 形成唯一完整四角色解，去重后 63 张成品板与人工真值一致；
- 20/20 满足连续几何阈值；
- 同族轨道 topology 相同只证明纵向站位包络相同，不能证明完整端链或板件几何相同。

逐对机器证据见
`output/box-proof/evaluation.json`，当前验证结论见
`docs/superpowers/reports/2026-07-19-box-region-proof-correction.md`。原 v2/v3
机器报告和 2026-07-18 验证报告均已标为失效；上面的迁移矩阵规则决定仍然有效。

## 11. 2026-07-19 整体复审增量

本节覆盖第 9 节的历史测试数量，不改变上方迁移矩阵的规则决定：

- BOX 编译只接受明确毫米单位；未知单位和不能证明为辅助对象的不支持实体 fail closed；
- 圆孔识别不再依赖 `Bolt`/`Hole` 图层名，最终归属仍使用 Shapely 覆盖关系；
- 视图候选支持块展开、物理图层改名及提示图层/通用图层混用，不把图层名或块作用域
  当作制造事实；
- 孔型保留独立来源作用域的观测实例数：单次观测不能复制，两次独立观测可以授权两个
  对称物理实例；
- 跨视图站位换算区分已配准纸面轴和独立旋转视图，二者均有变形回归；
- 监督评估逐对写出并重新打开 clean DXF，核对毫米单位、四板几何、孔、标签和 audit；
- 提升第二个文件时注入失败，可恢复所有既有正式输出；
- topology fallback 以端点连通和自适应几何长度筛选为主，不按图层分区；已覆盖物理投影线
  分散到 3 个和 9 个通用图层的变形，也允许闭合 LWPOLYLINE 作为一个完整投影轮廓
  参与连通分量。输入上限为 2048 个 topology facts、单连通分量 512 个 facts 和
  128 个候选组，超限时显式拒绝；
- 同等或接近的视图候选保留到有限域；只有第一、第二 pairing 的几何残差同时满足
  绝对和相对安全分差时才淘汰被支配候选，排序 ID 不授权几何；
- 纸面换算不能由轴方向或端点跨度接近单独授权；必须找到至少 3 个紧残差站位对应，
  其中至少 1 个是内部站位，并覆盖不少于共同站位跨度的 80%。未证明纸面配准时，
  站位增量只按目标视图局部纵轴换算；补偿过端点跨度的独立 1° 旋转仍会被拒绝；
- 非标注层圆可提供孔证据；标注层圆进入歧义通道并阻断自动制造，不会静默删除或
  直接物化为切孔；
- POINT 只在明确标注图层且同层存在受支持标注原语时视为辅助；HATCH 边界被有限
  展平、写入 Source Facts 和 fingerprint。只有整个填充与每个候选投影视图区域分离，
  或边界被同一来源作用域且已进入角色视图的物理投影线完整覆盖时才允许忽略；标注线
  不能授权 HATCH，判定也不依赖
  `Section`/`OtherObjectType` 层名；
- 监督几何去重同时比较孔中心和半径；保存后验证把每个标签文字的位置、内容与其板件
  Polygon 绑定，不再只比较标签集合；
- BOX 全集 `184/184`，20 组只读前后 DXF 的完整 proof 门禁通过；项目全量除既有
  便携 Python 子进程找不到 `pytest` 的环境测试外为 `292 passed`。

proof 当前是受信任本机进程边界内的完整性凭据，不是带外部私钥的身份签名。若部署要
抵抗能任意改写本机代码和 proof 的攻击者，必须另接制品签名基础设施。
