# BH/BOX 统一零件标记布局设计

## 目标

为 BH 与 BOX 输出建立同一套零件标记布局规则，修复窄板上实际 30 mm
文字能够放下、却被旧的比例包络误判为无法放置的问题，同时保持既有正常
输出的字号和制造几何不变。

## 规则

- BH 和 BOX 的标记文字仍由各自领域规则生成，共用模块只负责测量、选字号和定位。
- BH 使用紧凑标记：`p=<编号>腹`、`p=<编号>翼`；两块不等价翼板使用
  `翼-1`、`翼-2`，不在可见文字中追加数量后缀。
- 实际文字包络按 SimSun 近似宽度计算：ASCII `0.6 em`，全角/宽字符
  `1.0 em`，其他非 ASCII 字符 `0.8 em`。
- 合法包络为“实际文字矩形 + 上下左右各 5 mm”，不再按字号成倍增加空白。
- 最小字号为 30 mm。BOX 继续用原比例公式选择首选标准字号；若旧公式低于
  30 mm，则首选字号钳制为 30 mm，再用真实包络判断能否放置。
- BH 继续以翼板宽度计算首选字号，统一定位器只能保持或降低该字号，不能放大。
- 同一源图的所有板件使用同一字号。
- 依次尝试外轮廓质心、材料质心、材料代表点和归一化 `polylabel`；每个候选点
  必须通过材料区域对完整安全包络的 `covers` 检查。
- 材料区域为外轮廓减去全部圆孔和异形内孔。定位器不允许文字或 5 mm
  安全距离覆盖孔洞。
- 30 mm 仍无法放置时失败关闭，错误必须包含板件标识、文字、所需包络、
  材料边界尺寸、孔洞数量和安全距离。

## 组件边界

新增 `src/steel_dxf_split/part_mark_layout.py`，只依赖 Shapely，提供：

- `PartMarkTarget`
- `PartMarkPlacement`
- `PartMarkLayoutError`
- `label_em_width`
- `part_mark_envelope`
- `part_mark_clearance_envelope`
- `preferred_standard_part_mark_height`
- `layout_part_marks`

BOX writer 负责把 `LaidOutPlate` 转换成外轮廓和材料几何；BH writer 负责把
`BHPlate` 的 bulge 轮廓转换成 Shapely 几何。两个 validator 分别复用共享
包络函数检查保存后的真实文字位置和字号。

## 兼容性

- 不修改 BH/BOX 编译器、几何求解器、Manufacturing IR 或焊接余量算法。
- 不提高现有首选字号，因此既有 30 mm 输出仍保持 30 mm。
- BOX writer 不开放人工字号覆盖；BH 保留现有 `text_height` 参数，但它仅作为
  首选上限，最终仍必须通过共享安全布局。
- 新共享模块必须纳入 BOX 生产实现指纹和 BH 集成源码清单。

## 验收

- `a1-3-cb-356` 的 300 mm 宽 BOX 板件以 30 mm 标记通过布局和保存后验证。
- 文字真实包络及每边 5 mm 安全包络均完全位于材料区域内。
- 位于板中心的孔洞会触发确定性的避孔定位。
- 真实北邮 `a1-3-cb-356.dxf` 可以生成并通过保存后验证。
- BH 20 对与 BOX 20 对权威语料保持只读并全部通过。
- BOX 发布门重新生成与当前实现指纹绑定的认证。
- 完整测试、`compileall`、差异检查、wheel 构建和安装后冒烟验证通过。
