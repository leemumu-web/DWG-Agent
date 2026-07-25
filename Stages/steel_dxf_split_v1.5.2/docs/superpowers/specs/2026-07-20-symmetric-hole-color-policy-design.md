# 对称圆孔左红右白公共策略设计

## 1. 目标与已确认语义

本设计为 BH 与 BOX 拆板输出建立同一套孔洞颜色策略：

- 判定范围是每一块拆出板件自身的局部 X 方向；
- 局部 X 以制造 IR 已规范化、尚未排版的输出方向为准，不使用
  `LEFT_WEB`/`RIGHT_WEB` 等物理角色，也不使用源图视图方向；
- 只有被证明为左右镜像、且双向唯一配对的圆孔才属于“对称孔”；
- 每一对对称圆孔中，局部 X 较小的左孔使用 ACI 1（红色）；
- 对应右孔使用 ACI 7；
- 未配对孔、中线孔、歧义孔以及非圆形内轮廓全部使用 ACI 7；
- 不改变孔数量、孔位置、孔半径、板轮廓、标签、板件分组或制造语义。

ACI 7 是 CAD 的白/黑自适应色：深色背景通常显示为白色，浅色背景通常显示为黑色。本设计的机器合同是“ACI 7”，而不是屏幕像素必须为白色。

## 2. 适用范围

必须覆盖当前所有拆板写出路径：

1. BH 清洁 1:1 输出；
2. BOX 正式生产 REGION 输出；
3. BOX legacy/辅助清洁 1:1 输出；
4. BOX 复核 1:1 输出；
5. BOX 图纸比例输出。

第一阶段只对可证明的圆孔进行镜像配对。BH 的任意形状 `inner_contours` 不进入镜像算法，仍保留在 `CUT_HOLE` 图层并显式设置为 ACI 7。

以下内容不属于本次任务：

- 不改变孔洞或板件识别算法；
- 不改变 BH/BOX 制造 IR；
- 不改变 BOX 四板求解、分组、数量或发布授权逻辑；
- 不新增 `CUT_HOLE_RED`、`CUT_HOLE_WHITE` 等图层；
- 不处理任意非圆轮廓的镜像等价；
- 不安装新依赖；
- 不修改、移动或重命名任何前后金样目录及其文件。

## 3. 设计原则

### 3.1 公共纯策略，领域写出器只做适配

新增 `src/steel_dxf_split/hole_color_policy.py`。该模块：

- 不依赖 ezdxf；
- 不依赖 BH 或 BOX 数据模型；
- 不读写文件；
- 不修改传入对象；
- 输入板件 X 范围和圆孔几何；
- 返回与输入圆孔索引对齐的不可变颜色计划及配对诊断。

BH 与 BOX 只负责把各自的圆孔模型转换成 `(x, y, radius)`，不得各自重新实现左右判断或配对逻辑。

### 3.2 制造几何与展示颜色分离

颜色是交付展示合同，不写入制造 IR，也不改变制造语义 fingerprint。BOX 的生产实现 fingerprint 必须纳入公共策略文件，使策略代码改变时旧 release attestation 自动失效。

### 3.3 默认白色、证据充分才变红

`CUT_HOLE` 图层默认颜色统一为 ACI 7。每一个孔实体仍显式写入 ACI 1 或 ACI 7：

- 已确认对称对的左孔：ACI 1；
- 所有其他孔洞实体：ACI 7。

这样即使调用方漏做分类，结果也会安全地退化为白色，而不会把右孔或未知孔误染成红色。

## 4. 公共策略合同

建议提供以下最小公共接口，具体命名可以按项目风格微调，但语义不得改变：

```python
RED_ACI = 1
WHITE_ACI = 7

@dataclass(frozen=True, slots=True)
class SymmetricHoleColorPlan:
    colors_aci: tuple[int, ...]
    pairs: tuple[tuple[int, int], ...]  # (left_index, right_index)
    ambiguous_indices: tuple[int, ...]
    midline_indices: tuple[int, ...]

def plan_symmetric_circle_colors(
    holes: Sequence[tuple[float, float, float]],
    *,
    plate_min_x_mm: float,
    plate_max_x_mm: float,
    center_tolerance_mm: float = 0.01,
    radius_tolerance_mm: float = 0.01,
    midline_tolerance_mm: float = 0.01,
) -> SymmetricHoleColorPlan:
    ...
```

输入必须是板件尚未进行图纸比例缩放的物理 1:1 毫米坐标。排版平移不会影响判定，但写出器应在源板件上先生成颜色计划，再把同一索引计划应用到排版后的实体。图纸比例输出也必须先在 1:1 源板件上分类，不能在缩放后的图纸坐标中用固定 0.01 判断。

以下输入属于制造几何错误，应抛出 `ValueError` 并阻止输出：

- 非有限坐标或半径；
- 半径小于或等于零；
- 非有限板件范围；
- `plate_max_x_mm <= plate_min_x_mm`。

有效但无法证明对称关系的孔不抛错，只返回 ACI 7。

## 5. 镜像匹配算法

对每块板独立执行：

1. 计算局部对称轴：

   ```text
   mid_x = (plate_min_x_mm + plate_max_x_mm) / 2
   ```

2. 按圆心 X 划分：

   - `x < mid_x - midline_tolerance_mm`：左候选；
   - `x > mid_x + midline_tolerance_mm`：右候选；
   - 其余：中线孔，保持 ACI 7。

3. 对每个左候选 `(lx, ly, lr)` 计算镜像目标：

   ```text
   target = (2 * mid_x - lx, ly)
   ```

4. 左候选与右候选之间只有同时满足下列条件才建立候选边：

   ```text
   hypot(rx - target_x, ry - target_y) <= 0.01 mm
   abs(rr - lr) <= 0.01 mm
   ```

5. 只接受“双向唯一”候选边：

   - 该左孔只有一个右候选；
   - 该右孔也只有这一个左候选。

6. 不使用贪心最近邻。重复孔、多候选或冲突候选全部视为歧义，涉及的孔全部保持 ACI 7。

7. 初始颜色全部为 ACI 7；只把已接受配对中的左索引改为 ACI 1。返回的 `pairs` 按几何稳定键排序，结果不得依赖输入实体顺序。

当前真实输出回放中，最大镜像圆心残差约为 `0.000391 mm`。`0.01 mm` 约为该残差的 25 倍，同时仍远小于实际孔距，适合作为第一版固定合同。未经新样例证据不得放宽公差。

## 6. 写出链路接入

### 6.1 BH

修改 `bh_writer.py`：

- `CUT_HOLE` 图层默认颜色由 ACI 1 改为 ACI 7；
- 在每个源 `BHPlate` 上调用公共策略；
- 排版只允许平移，圆孔索引必须保持不变；
- 写 `CIRCLE` 时按颜色计划设置实体 `dxf.color`；
- 写 BH `inner_contours` 时显式设置 ACI 7；
- 不改变确定性保存、制造 fingerprint 或 GUID 规则。

修改 `bh_pipeline.py` 与 `bh_validator.py`：

- 把 writer 返回的布局传给保存后 validator，或提供等价的确定性布局闭环；
- 保存后重新打开 DXF；
- 按几何唯一匹配实际圆孔与预期排版圆孔，不依赖 DXF 实体遍历顺序；
- 验证左侧已配对孔为 ACI 1，右侧、未配对孔和所有非圆内轮廓为 ACI 7；
- 保留现有数量、闭合性和无辅助线检查。

### 6.2 BOX legacy 清洁、复核与图纸比例

修改 `box_writer.py`：

- `CUT_HOLE` 图层默认颜色改为 ACI 7；若图纸比例源文档已经存在该图层，也要把图层颜色规范化为 ACI 7；
- `write_clean_1to1()`、`write_review_1to1()`、`write_sheet_scale()` 都在原始 1:1 `assembly.plates` 上生成颜色计划；
- `stack_layout()`、`translated()` 和 `scaled_geometry()` 必须保持板件和孔洞索引顺序；
- `_add_plate()` 接收与 `plate.cut_holes` 等长的颜色序列并显式设置每个圆实体的 ACI；
- 图纸比例输出把源板件颜色计划应用到缩放后的对应孔，不在缩放坐标中重新分类；
- `STUD_REFERENCE` 不属于切孔，颜色逻辑保持不变。

修改 `box_validator.py` 与 `box_pipeline.py`：

- pipeline 向 validator 提供原始板件颜色计划和写出后的预期布局；
- validator 通过圆心、半径和板件归属把实际 `CUT_HOLE` 圆映射到预期孔；
- 增加颜色合同检查和红/白数量诊断；
- clean、review、sheet 三种 `output_kind` 使用同一检查；
- 任何颜色检查失败都加入 `failed_outputs`，临时目录内容不得提升到最终路径。

### 6.3 BOX 正式生产 REGION

修改 `box_delivery_writer.py`：

- 正式文档的 `CUT_HOLE` 图层默认颜色改为 ACI 7；
- 在布局前对每个 `BoxDeliveryPlateGroup.circular_cuts` 生成颜色计划；
- 布局仅平移 group，颜色计划按圆孔索引应用到布局后 cut；
- 正式 writer 与离线认证 candidate writer 必须走同一私有写出函数，不能产生两套颜色行为。

修改 `box_region.py`：

- `_add_region()` 增加显式 `color_aci` 参数；
- `add_circle_region()` 和 `add_polygon_region()` 透传颜色；
- 板外轮廓 REGION 继续使用 ACI 7；
- 圆孔 REGION 使用公共策略给出的 ACI 1 或 ACI 7。

修改 `box_delivery_validator.py`：

- 从同一 `BoxDeliveryBatch` 独立重算布局和颜色计划；
- 保存后重新打开并检查每个 `CUT_HOLE` REGION 的显式 ACI；
- 通过 REGION 边界几何与预期圆孔做双向唯一映射后再比较颜色，
  不依赖 DXF 实体遍历顺序；
- 验证 `CUT_HOLE` 图层为 ACI 7；
- 保留 REGION 数量、单面、边界、标签、legacy curve 禁止项和 writer closure；
- 增加右孔篡改为红色、左孔篡改为白色时的失败测试。

修改 `box_release.py`：

- 把 `hole_color_policy.py` 加入 `_PRODUCTION_SOURCE_FILES`；
- 不把颜色写入制造 IR fingerprint；
- 策略、writer 或 validator 发生变化后，当前 release attestation 必须因实现 fingerprint 漂移而失效，重新完成离线认证后才能正式生产。

## 7. 验证结果的数据合同

BH 与 BOX 保存后验证报告至少增加以下可机器判断的数据：

```json
{
  "checks": {
    "cut_hole_layer_is_white_aci7": true,
    "symmetric_hole_colors_match": true,
    "noncircular_cut_holes_are_white": true
  },
  "hole_color_counts": {
    "expected_red": 24,
    "actual_red": 24,
    "expected_white": 24,
    "actual_white": 24,
    "ambiguous": 0
  }
}
```

BOX 无非圆内轮廓时可以省略或固定通过 `noncircular_cut_holes_are_white`，但 BH 必须实际检查。报告字段名可按现有风格调整，语义和失败闭环不得省略。

## 8. 失败与安全策略

| 情况 | 结果 |
|---|---|
| 完整、双向唯一的镜像圆孔对 | 左 ACI 1，右 ACI 7 |
| 仅一侧存在圆孔 | ACI 7，不阻止制造输出 |
| 中线孔 | ACI 7 |
| 多候选、重复孔或冲突候选 | 涉及孔全部 ACI 7 |
| 非圆内轮廓 | ACI 7 |
| 非有限或无效圆几何 | 抛错，阻止输出 |
| 保存回读后颜色丢失或被改写 | validator 失败，不提升最终文件 |
| 未来引入旋转或镜像排版 | 不允许在变换后重分类；必须携带源颜色计划并新增相应测试 |

颜色分类失败不能通过“把全部孔染红”降级。未知情况只能白色或阻止输出。

## 9. 测试策略

遵循测试先行，至少覆盖以下测试。

### 9.1 公共策略单元测试

新增 `tests/test_hole_color_policy_v1.py`：

- 精确镜像对：左红右白；
- 圆心残差在 0.01 内：配对；
- 圆心残差超过 0.01：全部白；
- 半径残差在内/超限；
- 左一右多、左多右一和重复孔：歧义且全部白；
- 单侧孔、中线孔、无孔；
- 输入顺序打乱，按几何映射后的结果不变；
- 整块板和平移后的孔同时平移，结果不变；
- 非有限值、非正半径和无效板宽抛错；
- 统一缩放后的坐标不能用固定物理公差重新分类，确保写出器必须携带源计划。

### 9.2 DXF 写出与回读测试

- `CIRCLE`：ACI 1/7 保存回读保持；
- `LWPOLYLINE`：ACI 7 保存回读保持；
- `REGION`：ACI 1/7 保存回读保持；
- `CUT_HOLE` 图层默认 ACI 7；
- 实体必须显式为 1 或 7，不接受 BYLAYER 256 代替实体合同；
- 保存文件中孔数量、圆心、半径及 REGION 边界与改动前一致。

### 9.3 BH 回归样例

至少验证：

| 样例 | 预期红孔 | 预期白孔 |
|---|---:|---:|
| `2b1-cb-29` | 24 | 24 |
| `2t1-cb-4` | 32 | 32 |
| `2t2-cb-37` | 24 | 24 |
| `3t2-cb-13` | 32 | 32 |
| `2b2-cb-10` | 22 | 42 |

当前只读语料回放覆盖 728 个圆孔，识别 134 对、歧义 0；实现后应把该结果作为回归基线，而不是在生产代码中硬编码文件名或数量。

### 9.4 BOX 集成测试

- 正式 REGION 输出中的对称圆孔左红右白；
- clean/review/sheet 输出得到相同的语义颜色签名；
- 当前 BOX 已验证输出中的 36 个非对称圆孔全部保持 ACI 7；
- 图纸比例输出在缩放前后保持同一孔索引颜色；
- 改变 CIRCLE 或 REGION 的实体遍历顺序不影响验证；
- 把任一右孔篡改成 ACI 1，validator 必须失败；
- 把任一已配对左孔篡改成 ACI 7，validator 必须失败；
- 颜色改变不得影响 REGION 几何闭环、正式路由或原子提升。

### 9.5 全量回归

- 运行新增针对性测试；
- 运行全部 BH 与 BOX 测试；
- 运行项目全量测试；
- 如项目已有 lint、类型检查或 OpenSpec 校验，继续运行现有命令；
- 对只读 BOX 前后金样重新执行几何验收，确认颜色改动没有改变几何结果；
- 对金样目录计算任务前后哈希，必须完全一致。

## 10. 验收标准

任务完成必须同时满足：

1. 公共策略只有一个实现，BH/BOX 不存在第二套镜像配对逻辑；
2. 所有 `CUT_HOLE` 图层默认 ACI 7；
3. 所有生成的切孔实体显式使用 ACI 1 或 ACI 7；
4. 已确认对称圆孔严格左红右白；
5. 未配对、中线、歧义和非圆孔全部白色；
6. BH、BOX production、clean、review、sheet 保存回读验证全部通过；
7. `2b1-cb-29` 达到 24 红、24 白；
8. 728 孔回放基线达到 134 红、594 白、0 歧义；
9. 制造几何、板件数量、孔数量和标签无变化；
10. BOX 实现 fingerprint 包含公共策略，旧认证在代码变化后正确失效；
11. BOX 前后金样目录没有任何写入或哈希变化；
12. 不覆盖当前工作树中与 BOX 交付认证有关的未提交改动；
13. 不提交、不推送，除非用户另行明确授权。

## 11. 实施边界

Claude Code 执行时必须以当前工作树
`D:\Dev\Projects\dxf agent\worktrees\box-completion`
为基线，先阅读并保留其中现有的 BOX delivery/release WIP。应采用小步测试先行修改，不得回滚、覆盖或重建当前未提交文件。

本设计中的颜色数量来自当前只读回放，用于验证策略，不构成几何金样的替代。BOX 拆板算法与最终几何验收仍以现有前后 DXF 金样及当前项目门禁为准。
