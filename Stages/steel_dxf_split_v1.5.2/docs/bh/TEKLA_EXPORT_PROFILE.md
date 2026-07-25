# Tekla BH DXF 导出 profile 契约

## 1. profile 不是通用 Tekla 真理

Tekla Structures 的 DWG/DXF 导出允许通过图层规则把不同图纸对象映射到不同图层，因此 `Part`、`Bolt`、`PartMark` 等名称是当前项目 profile 的已验证方言，不是所有 Tekla 工程的固定约定。

当前权威 profile ID 为：

```text
project_tekla_bh_dxf_v1
```

当前实际来源链固定为：

```text
Tekla drawing -> object-grouped DWG -> DXF
```

编译入口只有最终 DXF，没有模型 JSON、Tekla Open API sidecar 或可依赖的自定义对象数据。因此重复的 `Part` 容器应理解为同一单构件的不同投影，不是三块已经展开完成的制造板。编译器只能使用 DXF 中仍可验证的对象分组、可见/隐藏线型、几何拓扑、跨视图关系和 BH 标注；未编码的模型/图纸设置不得猜测补齐。

调用方必须用 `--authorize-tekla-bh-single-part-profile project_tekla_bh_dxf_v1` 显式声明输入来自该工作流。编译器不会通过“刚好看到 Part/Bolt 图层”就猜测 Tekla 来源或自动授权。来源契约只限定语法范围，每个板边、孔、开口和属性仍需要逐项证明。

材料表截面必须明确以 `BH` 开头。滚轧或其他 H 系列前缀不是当前焊接 BH 语法的别名，即使尺寸字段恰好也写成 `H*B*tw*tf`，入口和元数据关联仍会拒绝它。

Tekla 官方参考：

- [Layers in exported DWG/DXF drawings](https://support.tekla.com/doc/tekla-structures/2020/int_layers_in_drawings_exported_to_dwg_dxf_files)；
- [Example: setting up layers and exporting to DWG](https://support.tekla.com/doc/tekla-structures/2026/int_example_setting_up_layers_and_exporting_to_dwg)；
- [Export a drawing to 2D DWG or DXF](https://support.tekla.com/doc/tekla-structures/2020/int_exporting_drawings_to_2d_dwg_anddxf_files)；
- [Object-based drawing export](https://support.tekla.com/cs/doc/tekla-structures/2023/int_export_to_dwg_or_dxf)；
- [Single-part drawings](https://support.tekla.com/doc/tekla-structures/2021/dra_single_part_drawings)。

## 2. 当前方言映射

`src/steel_dxf_split/bh_dialect.py` 是可执行的 profile 定义。下表列出当前映射及其权限：

| 导出图层 | 允许的实体 | 前端角色 | 制造权限 |
|---|---|---|---|
| `Part` | LINE, ARC, CIRCLE, LWPOLYLINE, POLYLINE | `part_edge` | 可作为板轮廓和闭合面的源几何；仍需视图归属、拓扑和来源证明 |
| `Bolt` | CIRCLE, ARC, LWPOLYLINE, POLYLINE | `physical_cut` | CIRCLE 可作为物理圆孔候选；闭合轮廓可作为异形开口候选；需要唯一归属和包含验证 |
| `Bolt` | LINE, XLINE, RAY, POINT | `cut_helper` | 只用于孔的边视/十字符号和关联证据，绝不直接写入切割输出 |
| `PartMark` | 文本/引线等 | `part_mark` | 构件/板件标记关联，用于交叉验证 |
| `BoltMark` | 文本/引线等 | `bolt_mark` | 孔径、数量/节距等独立支持或冲突证据 |
| `Z-DIMENSIONS`、`Z-DIMENSIONS-LINES` | DIMENSION 及相关文本/点/线 | `dimension` | 前者可承载文字和定义点，后者是新版 DWG/DXF 链拆出的尺寸线；共同为视图范围、轮廓、孔链和展开总长度提供独立测量 |
| `Section` | 剖面符号/文本 | `section` | 约束视图关系，不直接变成板边 |
| `DrawingSheet` | 图框/页面实体 | `drawing_sheet` | 仅保留上下文 |
| `OtherObjectType` | 其他 | `other` | 仅保留上下文 |
| 未映射图层 | 任意 | `unknown` | 不得授权制造几何 |

Tekla/DWG/DXF 链路可能把直径符号写成 MIF（例如 cp936 的 `\\M+5A6B5`）、`%%c`，或旧单字节解码结果 `¦µ`。[AutoCAD DXF 字符串存储规范](https://help.autodesk.com/cloudhelp/2019/ENU/AutoCAD-DXF/files/GUID-2553CF98-44F6-4828-82DD-FE3BC7448113.htm)明确把 MIF/Unicode 控制序列归入字符串编码层。这些传输方言应在数字取得工程含义前统一为 `Φ`。孔标语法必须包含明确直径符号；`16-22`、未解码的反斜杠转义或“数量 + 任意分隔 + 数字”不能被推断为 16 个直径 22 mm 的孔。属于选定视图但仍无法解析的 `BoltMark` 必须保留并触发关键证据缺失，不能按“没有孔标”放行。

同一 MIF 机制也承载一般中文，例如“零件编号”。预览不得绕过语义层已经验证的传输解码：读取正确代码页后，所有嵌套 TEXT、MTEXT、ATTRIB 和 ATTDEF 先执行共享 MIF/DXF Unicode 解码，再把实际中文绑定到可用的 Linux CJK 字体。显示解码不删除 MTEXT 布局控制，也不参与制造判断；无效转义保持原样并在专项审计中暴露。

Tekla 的对象化二维导出允许把 mark text、frame 和 leader 分层输出；旧导出的 `Cut lines with text` 还会在文字或图纸标记处截断连续线（[Tekla 当前对象化导出](https://support.tekla.com/doc/tekla-structures/2026/int_export_to_dwg_or_dxf)，[Tekla 旧导出对象分组与截线设置](https://support.tekla.com/doc/tekla-structures/2026/int_exporting_drawings_to_2d_dwg_anddxf_files)）。因此 `Part` 轮廓的局部开缝只有在同一 mark 对象组、文字/标注线覆盖、共线两端、对侧轮廓和端部封口全部成立时，才可解释为导出遮挡并建立推断桥；一般几何缺口仍然拒绝。

`OtherObjectType` 已是本 profile 明确分类的非制造上下文，不再进入“可能遗漏 Part 投影”的候选闭包检查；只有真正未映射的 `unknown` 几何保留这项阻塞权力。这样既不把材料表框线误认成板材，也不削弱对未知物理图层的失败关闭。

当前 profile 已验证两种等价的隐藏投影线型拼写：旧版对象化导出的
`XKITLINE04`，以及部分 AutoCAD 2018/AC1032 DWG→DXF 转换使用的 `DOT2`。两者在
源图中均表现为 `Part` 层点状非连续线、隐藏线颜色/线宽，并且单独不能形成闭合面；
把它们加入可见轮廓只会切碎由连续实体边形成的板面。SourceIR 仍保存原线型，进入
几何 lowering 时才按 `VisibilityClass.HIDDEN` 规范化，因此几何算法不依赖某个
DXF 线型名字。隐藏实体默认不是可见板边；只有像圆弧恢复这样的显式规则在完整短弦
链端点、半径和扫掠一致时，才能将隐藏 ARC 作为精确制造证据。

该规则不是“所有虚线都是隐藏边”：`Continuous`、`XKITLINE00` 仍为可见物理边，
未验证的 `HIDDEN2` 等线型不会被猜测性排除。新增线型别名属于方言权限变化，必须写入
方言指纹并重新绑定 release evidence。相同原则也适用于图层映射：
`Z-DIMENSIONS-LINES` 由完整尺寸线组、箭头/延伸线几何和 11 图一致性证明为尺寸角色；
其他相似但未列入 profile 的图层继续保持 `unknown`。

`Part` 层也不是“每条可见线都直接下料”。投影边不等于制造切割边：轮廓线、面边、圆角边、倒角标记、polybeam 过渡和相邻重合投影必须先在 `TeklaPartProjectionIR` 中取得角色。局部来源斜边的权威性高于矩形翼缘先验；短边或小角度本身不是噪声。只有整幅投影一致的坐标旋转或当前 polygonize 网格内的坐标量化可以在视图层解释，不能逐边把局部斜度扶成水平/垂直。

`longitudinal_projection_overlay` 是 DXF-only 契约中的受限投影角色：仅当亚毫米反向回折被一条继续覆盖大部分构件边界的来源 LINE 穿过时，回折带内的 LINE 才可从直接切边证据降级为面边/可见性叠线。局部真实斜边、普通窄缺口，或同一轮廓其他位置的微斜边不会获得此降级权限。

## 3. 导出前检查

生产工作流应固定下列条件：

1. 从 Tekla 单构件图导出，每个输入只对应一个焊接 BH 构件；
2. 使用已审批的对象→图层规则，不在导出后临时改图层名；
3. 保留可见/隐藏线型区分，保留真实 CIRCLE/ARC/LWPOLYLINE 实体，不用通用折线粗暴替换所有曲线；
4. 保留材料表的空间行关系、构件号、BH 截面、材质、长度和比例文本；
5. 保留 DIMENSION、孔标、零件标和剖面标记的位置/旋转/字高，不只导出显示字符串；
6. 当前生产契约中构件长轴平行全局 X 轴，允许平移与 X/Y 镜像，不允许未验证的任意旋转；
7. 在发布验证中保留原始源图字节和 SHA-256，不用 CAD 软件重存后的文件替换已审批语料。

`$INSUNITS`、图纸比例文本和导出后的模型空间尺寸必须与当前 profile 一致。编译器根据已验证图纸关系处理空间尺寸，不会因为一个“常见比例”就对几何整体乘固定系数。

## 4. 尺寸、构件长度与展开的权限

DIMENSION 对象的真实 measurement、显示值和其空间指向对证明很重要。编译器会区分视图外形尺寸、孔链尺寸和可绑定的展开总长度，不是看到一个接近的数字就当作全局授权。

Tekla 的 polybeam/弯曲构件长度与参考线、软件设置和展开规则有关，并非总是水平投影或外包框宽度。官方参考：

- [How can I control the length of polybeams and curved beams?](https://support.tekla.com/article/how-can-i-control-the-length-of-polybeams-and-curved-beams)；
- [Unfolding corner ratio settings](https://support.tekla.com/cs/node/135552)。

因此材料表 `LENGTH` 只是展开路径的支持性一致通道。若 DXF 没有编码 `unfold_corner_ratios`、参考线长度策略或每个不同轮廓可用于制造的总长度，几何仍依赖未知 Tekla 展开策略，`BH.PROOF.FLANGE.DEVELOPMENT` 必须为 `missing` 并进入复核。编译器会保留 DXF 已画出的斜边，但不会由此虚构未导出的展开参数。

## 5. 新 profile 如何取得生产权限

新项目、新 Tekla 版本、新导出规则或新图层名不得通过在默认 profile 中随意加别名直接进入生产。应完成：

1. 给 profile 分配新的稳定 ID；
2. 保存导出设置、Tekla 版本、对象→图层规则、线型、单位和比例的可审计记录；
3. 建立包含代表性等高/变高、无孔、重叠翼缘、异形开口、端部破碎和圆弧的源图语料；
4. 让专业人员独立确认三物理板、外轮廓、孔/开口、厚度、材质和展开，并固定源/人工哈希；
5. 用 `BHDialectProfile` 表达图层+实体类型规则，不用样本号或几何数字特例；
6. 运行完整语料、负例、平移/镜像/INSERT 展开严格不变性、重复编译、writer 字节、保存后验证和物理路由门禁；
7. 从通过的发布 summary 生成绑定编译器版本、方言指纹、工程知识版本、语料哈希和能力产物哈希的 release evidence；
8. 在代码评审和工程审批后才把新 profile ID 加入可信发布列表。

人工拆板只用于离线验证。新 profile 的 ground-truth firewall 必须与当前 profile 保持相同强度。
