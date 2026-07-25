# BH 语义编译器架构与信息模型

## 1. 为什么要重构

v0.5–v0.7 已能稳定通过 14 组监督样本，但核心逻辑仍存在三个组织问题：

1. DXF 展开后很快降维为裸实体列表，来源信息容易丢失；
2. 材料表、视图和板件选择隐含在函数局部变量中，缺少候选和解释；
3. 验证主要发生在最终输出端，无法明确区分“读取错误、语义错误、几何错误、输出错误”。

v0.8 将系统拆分为和编译器相似的中间层。

## 2. 中间表示

### 2.1 Source IR

```text
BHDocumentIR
├── dxf_version / encoding / units
├── BlockIR[]
│   ├── INSERT handle / block name
│   ├── EntityAtom[]
│   │   ├── SourceRef
│   │   ├── semantic_layer
│   │   ├── visibility
│   │   ├── world bbox
│   │   └── transformed DXF entity
│   └── TextAtom[]
│       ├── raw / normalized text
│       ├── position / height / rotation
│       └── SourceRef
└── audit and corpus summary
```

Source IR 不作“这一定是腹板”的结论，只保存标准化事实。

### 2.2 Semantic IR

语义阶段产生：

```text
BHMetadata
AnnotationModel
ViewSelectionResult
DecisionRecord[]
```

每个决定包含：

```text
selected
score
confidence
margin
alternatives
Evidence[]
warnings
```

因此语义理解不是一个不可见的布尔判断，而是可检查的候选求解过程。

### 2.3 Manufacturing IR

```text
BHAssembly
├── metadata
├── web_plate
├── flange_plates[]
└── diagnostics

BHPlate
├── role
├── contour
├── thickness
├── quantity
├── circular_cuts[]
├── inner_contours[]
├── area
└── provenance
```

Manufacturing IR 不再包含尺寸线、十字线、剖切线或标题栏图元。

## 3. 编译 Pass

### FrontendPass

- 加载已审计 DXF；
- 展开嵌套 INSERT；
- 应用平移、旋转和缩放；
- 分类图层、线型和实体；
- 保存来源关系。

### AnnotationPass

- 识别尺寸观察；
- 识别孔标记；
- 识别零件标记；
- 统计剖面块。

### MetadataPass

- 定位包含 BH 截面的材料表块；
- 按文字 Y 坐标组成表格行；
- 按 X 顺序解析字段；
- 输出候选块、分数和置信度。

### ViewResolutionPass

- 枚举全部 Part 视图块；
- 对所有“主视图—翼缘视图”有序对评分；
- 保留前若干备选；
- 根据分数间隔给出置信度。

### GeometryLoweringPass

- 对 Part 实线执行精度网格吸附；
- 节点化交点并 polygonize；
- 选择或合并腹板面；
- 扩展翼缘端部面；
- 处理重叠投影；
- 计算变高度或折线翼缘展开；
- 恢复 ARC 为 bulge；
- 将孔和内开口归属于具体板件；
- 生成局部 1:1 板件。

### ValidationPass

- 标注语义交叉验证；
- 板件物理约束；
- 外轮廓和内开口拓扑；
- 圆孔完整圆盘包含；
- 孔间非重叠；
- 截面厚度和翼缘宽度；
- 数量守恒；
- 最终编译置信度。

## 4. 置信度不是正确率

置信度用于表达“当前图纸中支持该判断的证据强弱”，并不代替几何验证。

例如：

- 材料表行完整且唯一：高置信；
- 两个视图候选分数接近：降低视图置信；
- 没有 BoltMark：证据覆盖较低，但不视为矛盾；
- BoltMark 标注 Φ33，而实际只识别到 Φ26：明确矛盾；
- 几何约束失败：停止输出，而不是仅降低置信。

## 5. 现有信息利用矩阵

| 信息 | 当前用途 | 强度 |
|---|---|---|
| INSERT/块 | 视图和表格分区、来源追踪 | 主证据 |
| Part 图层 | 板件轮廓候选 | 主证据 |
| Bolt/CIRCLE | 实际孔 | 主证据 |
| Bolt/LINE | 边视孔归属消歧 | 弱语义证据 |
| XKITLINE04 | 受限边界桥接 | 条件证据 |
| 文字位置 | 材料表行解析 | 主证据 |
| 尺寸文字 | 长度/高度观察 | 交叉验证 |
| BoltMark | 数量和孔径 | 交叉验证 |
| PartMark | 零件号一致性 | 交叉验证 |
| Section | 当前仅统计 | 待深化 |
| 材料表重量 | 尚未用于 BH | 待深化 |
| 图纸说明 | 尚未形成规则图 | 待深化 |

## 6. 下一步泛化方向

### 完整尺寸约束图

将每个尺寸块转换为：

```text
DimensionConstraint
├── endpoints
├── direction
├── displayed_value
├── measured_value
├── scale
└── source evidence
```

这样可以在几何边缺失时，用尺寸约束辅助重建，并检测错误尺寸。

### 投影视图关系图

使用 Section、中心线和相对布局建立：

```text
ProjectionGraph
View A --projects-to--> View B
Feature x --corresponds-to--> Feature y
```

这将比单纯包围盒匹配更适合多视图和多个构件图纸。

### 质量守恒

在材料表包含单重和数量时，建立：

```text
Σ(plate area × thickness × density × quantity)
≈ material-table unit weight
```

作为候选组合的全局评分项。

### 多构件图纸

使用 PartMark、局部视图空间聚类和材料表多行，将一张图拆为多个独立编译单元。
