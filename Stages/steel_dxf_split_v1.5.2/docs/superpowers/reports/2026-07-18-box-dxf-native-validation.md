# BOX DXF 独立多视图方案验证报告

> **历史报告，已失效。** 2026-07-19 发现本报告引用的 REGION 读取器在
> `2b2-cb-145` 和 `2b2-cb-155` 中各静默漏掉一张真实成品板，因此下文原始
> “20/20”不能继续作为验收证据。修复、重新计数和新 proof 见
> `docs/superpowers/reports/2026-07-19-box-region-proof-correction.md`。

## 结论

本轮已经把 BOX 正式代码路径从旧 `extract_box_assembly()` 和所有 `bh_*` 领域模块中
解耦。新路径为：

```text
ezdxf
→ BoxSourceFactsV1
→ BoxMetadataEvidence
→ BoxGeometryIR
→ BoxViewIR
→ BoxPlateHypothesis
→ 完整四板 solver
→ BoxManufacturingIR
→ SplitAssembly 适配
```

独立算法已经完成 20 组结构性回放：20/20 形成唯一完整四板解，20/20 的板数、
角色、孔数、孔归属和批次结构与人工真值一致，20/20 同时满足当前连续几何
比较阈值。

这里的“视图”是 DXF 图纸内按 `INSERT`/实体来源和几何跨度识别出的二维投影区域
角色，不是 DWG/CAD view object，也不依赖 CAD 视图名称或主视图/俯视图元数据。

当前合同状态仍保持：

- `hard_pass=true`：只表示内部事实、来源、视图、假设和四板解的结构门通过；
- `runtime_authorized=false`：没有有效监督 gate proof 时，正式 pipeline 在写文件前拒绝；
- `production_ready=false`；
- `review_required=true`。

这一区分仍然必要。算法完成和离线结构验证不等于监督 manifest 已获人工批准；
没有有效 gate proof 时，pipeline 继续在正式写出前 fail closed。

## 20 组逐对结果

本次使用项目外只读镜像，内容来自用户指定的权威前后样例：

```text
D:\DevData\BOX拆板前后数据
```

机器报告：

```text
docs/superpowers/reports/2026-07-18-box-corpus-audit-v3.json
```

本次比较使用 2 mm 边界/bbox、`1e-5` 相对面积（并允许 2 mm 边界带对应的面积差）、
2 mm 孔心和 0.1 mm 孔半径阈值。该阈值只用于离线连续几何比较；板数、角色、
孔数、孔归属、来源和唯一解门禁没有放宽。

| 状态 | 数量 | 样例 |
|---|---:|---|
| 结构一致且满足连续几何阈值 | 20 | 全部样例 |
| 结构一致、仅连续几何超阈值 | 0 | — |
| solver 人工复核或拒绝 | 0 | — |
| 板数、角色、孔数或孔归属不一致 | 0 | — |
| 审计程序异常 | 0 | — |

`h-4-cb-38` 原有差异来自跨投影区域外推时混用了两个局部坐标系。修复后其最大
bbox/边界偏差为 `0.877723 mm`，最大孔心偏差为 `0.000092 mm`，孔半径偏差为
`0.003016 mm`；全部连续几何和结构检查均通过。纸面坐标换算必须由至少 3 个紧残差
站位对应共同证明，其中必须包含内部站位且覆盖不少于共同站位跨度的 80%；方向和
端点跨度接近都不能单独授权。真实 `h-4-cb-38` 提供 24 个对应、22 个内部对应，
最大配准残差约 `0.000014 mm`。未证明配准时只使用目标视图局部站位。1°、5°、6°
和 60° 独立旋转回归均连续一致，补偿过端点跨度的独立 1° 负例也不会获得纸面配准。
规则不包含样例名称、固定坐标或监督真值回读。

监督 gate 仍为 `unverified`，原因是 manifest 和 production proof 尚未人工冻结，
而不是算法仍缺少四板结构解。

## 已证明的能力

- facts 有限输入、非有限数拒绝、确定性 `source_key` 和 SHA-256 fingerprint；
- HATCH 边界被有限展平并纳入 Source Facts 和 fingerprint；
- 编译入口仅接受明确的毫米单位（`$INSUNITS=4`）；未知或其他单位 fail closed；
- 未进入 facts schema 且不能证明为辅助对象的实体类型 fail closed，不再静默忽略；
- DXF handle 不参与身份、候选、求解或输出选择；
- BOX 元数据按同一来源作用域和文字局部轴解析，不使用文件名回退；
- 20/20 源图的 `BOX H*B*tw*tf`、名义长度、材质和比例均能解析；
- 文字固定 `can_authorize_geometry=false`；
- LINE、ARC、CIRCLE、LWPOLYLINE 的 Shapely 几何 IR；
- 闭合 LWPOLYLINE 可以独立形成 topology fallback 的投影视图轮廓；
- 标注层圆形成歧义证据并阻断自动制造，不能直接成为切孔；
- `XKITLINE04` 隐藏线不会污染直接物理轮廓；
- 视图角色不依赖图纸绝对左右/上下位置，支持整体平移和旋转；
- 容差策略由实体自身尺度导出，不受世界坐标平移影响；
- 局部坐标轴对 90° 旋转稳定，制造语义不依赖轴符号抖动；
- 开放投影链、站位轨道、端链、外表面和可见/隐藏投影均是一等证据；
- 直接闭合轮廓与多视图闭环进入同一个完整装配求解器，不按来源模式硬编码角色；
- 原图只有投影线时，可以形成带边级 provenance 的多视图板件假设；
- 两块同族板可以具有不同长度、端部形状和孔模式，不再强制复制一个原型；
- 投影闭环和直接轮廓证据同强时，按完整主证据、无回退和最小闭环选择；
- 同形板可在已证明 BOX 截面拓扑下实例化为两块物理板；
- 上下翼缘、左右腹板交换归一化；
- 多解、缺少截面拓扑、缺少来源和边 provenance 时 fail closed；
- 拆板后真值 DXF 的闭合 LWPOLYLINE 与 ACIS REGION 均可离线读取；
- REGION 解析支持当前语料里的直线和 ellipse-curve 圆弧；
- bbox 只用于尺寸摘要和预筛；参考孔嵌套与几何比较使用 Shapely；
- pipeline 不调用旧 BOX extractor 或影子合同；
- 真实 `2b1-cb-86` 在整体旋转 90°、平移、实体倒序、handle/path 全部改变后，
  Source Facts fingerprint 按表达变化而改变，但四板制造语义 fingerprint 不变；
- BOX 输出仍采用同盘暂存、全部回读后原子提升；
- 没有有效监督 proof 时，pipeline 不留下任何正式输出。

## 已证明范围与剩余边界

当前 20 组已覆盖并证明：

- 现成闭环与纯多视图投影混合表达；
- 两块非同形翼缘、两块非同形腹板；
- 斜端、折线端、偏置端、端链和变长度站位；
- 可见线、隐藏线和截面宽度共同证明板边；
- 有孔/无孔及孔唯一归属；
- 独立块实例中的重复孔观测可证明孔型实例数；单次孔观测不得被复制到两块板；
- 辅助线污染下的候选拒绝和完整装配求解。
- 块作用域展开、物理图层改名、投影线分散到 3/9 个通用图层及两投影区域独立旋转；
- topology 输入、连通分量和候选组具有有限预算，超限 fail closed；
- HATCH 只有在边界/填充与所有候选投影视图区域分离，或其边界被同一来源作用域且
  已进入角色视图的物理投影线完整覆盖时才允许忽略；标注线不能授权 HATCH，巨大覆盖
  和板内小型 HATCH 均 fail closed；
- 同外形同孔数但孔位或孔径不同的板不会被监督去重；
- 保存后标签内容和插入位置必须绑定到同一块期望板件 Polygon。

仍未宣称覆盖的输入包括 ELLIPSE/SPLINE 等尚未进入 facts schema 的曲线、非均匀缩放
导致的曲线语义变化、未知单位、无法建立截面拓扑、非等价多解、孔归属不唯一，以及
超出 20 组制图分布的全新视图表达。这些情况继续 `manual_review` 或 `reject`。

## 测试证据

全部 `tests/test_box*.py`（事实、几何 IR、视图 IR、投影、重建、solver、制造 IR、
监督比较和 metamorphic）为：

```text
184 passed
```

项目全量套件排除一个已知便携 Python 环境项后为：

```text
292 passed
```

未排除运行只有
`test_pytest_worker_bypasses_hanging_interpreter_finalizer` 失败：该测试启动子进程时
主动把 `PYTHONPATH` 覆盖为仅项目 `src`，当前便携 Python 的 `pytest` 来自外部
site-packages，因此子进程报 `ModuleNotFoundError: pytest`。这与 BOX 代码无关，本轮
没有为 BOX 任务修改 BH worker 或其测试契约。

## 生产门

生产授权必须同时满足：

1. 20 组 manifest 的 SHA-256、10/10 分区和人工审批状态有效；
2. 20/20 结构 verdict 与 20/20 连续几何 verdict 被明确审核；
3. 验收阈值冻结后不再调参；
4. gate proof 同时绑定 manifest、逐对 verdict 和实现 fingerprint；
5. pipeline 加载 proof 时复核全部绑定关系。

当前 proof 的威胁边界是受信任的本机进程与文件系统：它提供内容完整性、代码/依赖
漂移和 corpus 漂移检测，不宣称抵抗能够任意改写本机代码和 proof 文件的恶意主体。
若部署环境需要对抗这种本机攻击者，必须由外部密钥或制品签名系统签署 proof；当前
仓库没有私钥基础设施，因此不能把普通 SHA-256 完整性绑定描述成密码学身份认证。

当前独立算法开发和结构验证已经完成，但 production proof 尚未人工冻结；在冻结前，
正式 pipeline 仍按设计拒绝自动生产写出。
