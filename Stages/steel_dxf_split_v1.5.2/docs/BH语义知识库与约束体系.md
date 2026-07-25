# BH 语义知识库与约束体系

## 知识库目标

`BHKnowledgeBase` 将过去散落在代码条件中的工程知识集中为显式配置：

- 物理组成；
- 图层和实体语义；
- 制造容差；
- 候选搜索范围；
- 评分权重；
- 自动化阈值。

知识库本身不读取 DXF，也不生成几何。

## 物理公理

```text
BH 构件 = 1 块腹板 + 2 块物理翼缘板
```

两块翼缘可以：

- 几何相同，Manufacturing IR 中表示为一个几何、数量 2；
- 几何不同，表示为两个几何、数量各 1；
- 几何相同但切割特征不同，必须拆成两个物理板对象。

## 观察语义

| 来源 | 默认语义 | 是否直接输出 |
|---|---|---:|
| `Part` 可见 LINE/ARC | 物理边界证据 | 否，需重建闭环 |
| `Part` 隐藏 LINE | 隐藏投影证据 | 否 |
| `Part` 隐藏 ARC | 精确圆弧证据 | 仅经验证后转为轮廓圆弧 |
| `Bolt/CIRCLE` | 真实圆孔候选 | 是，经归属和验证后 |
| `Bolt/LINE` | 中心线或边视孔语义 | 否 |
| `Z-DIMENSIONS` | 尺寸一致性证据 | 否 |
| `PartMark` | 零件身份一致性证据 | 否 |
| `BoltMark` | 孔数量/直径一致性证据 | 否 |
| `Section` | 视图关系证据 | 否 |

## 硬规则

- `BH.HARD.DISTINCT_PROJECTIONS`
- `BH.HARD.COMPLETE_PHYSICAL_DECOMPOSITION`
- `BH.HARD.MANUFACTURING_GEOMETRY_VALID`
- `BH.HARD.PROVENANCE_COMPLETE`

硬规则不参与折中。任何一项失败，候选不可选择。

## 软规则

- `BH.SOFT.PROJECTION_FIT`
- `BH.SOFT.ANNOTATION_CONSISTENCY`
- `BH.SOFT.ANNOTATION_COVERAGE`
- `BH.SOFT.MINIMUM_GEOMETRIC_REPAIR`
- `BH.SOFT.LONGITUDINAL_PLAUSIBILITY`
- `BH.SOFT.EVIDENCE_TRACEABILITY`

软规则的目的不是让几何“接近某个样本”，而是在多个物理合法方案之间选择证据更完整、修复更少的方案。

## 权重原则

1. 物理不变量高于标注；
2. 几何来源高于名称猜测；
3. 标注可以否定不一致方案，但缺少标注不能凭空否定清晰几何；
4. 修复代价只能排序合法方案，不能使非法方案合法；
5. 置信度与自动化处置分开：合法不等于可无复核自动生产。

## 泛化原则

新增样本暴露问题时，优先问：

- 是否缺少一种事实类型？
- 是否缺少一种几何关系？
- 是否缺少一个工程不变量？
- 是否需要保留多个候选更久？
- 是否需要新的特征归属规则？

禁止以构件号、文件名或固定坐标加入分支。
