# BOX 制造交接、REGION 交付与离线认证合并设计

> **历史记录，已废弃（2026-07-21）：** 本设计基于旧 BOX 算法和 REGION
> 交付，不再是当前架构。当前设计为
> `2026-07-20-box-true-source-fusion-design.md`。

## 1. 状态与依据

本设计把《BOX 拆板合并优化报告》中已经确认的候选 1、2、3 合并为一次收口：

1. 建立唯一、规范化的制造交接模型；
2. 建立强生产路由和 ACIS REGION 交付闭环；
3. 将 20 对金样认证从生产运行时推理中移出。

设计以当前工作树的独立多视图重建、完整四板求解和来源追踪为算法主体。外部
`box-dxf-split v0.2.1` 仅提供架构行为参考；由于双方仓库均未声明许可证，本项目
不复制其源代码，而是依据本项目数据模型独立实现。

以下只读目录仍是最终验收权威：

```text
D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf
D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf
```

生产算法不得读取拆板后 DXF。金样只允许由离线认证命令读取，不得修改、移动或
重命名。

## 2. 总体决策

合并后的数据流为：

```text
源 DXF
→ BoxSourceFactsV1
→ 多视图重建与完整四板求解
→ BoxManufacturingIR（四个物理角色）
→ BoxDeliveryBatch（唯一规范化制造交接）
→ 路由授权
  ├─ production_ready → auto_accepted → REGION 正式交付
  └─ review_required  → review_required/<构件> → 复核候选
→ 保存后重新打开、几何闭环验证
```

离线认证是另一条数据流：

```text
只读拆板前后金样
→ manifest 完整性校验
→ 20 对逐对编译、几何比较和保存回读
→ 离线评估报告
→ 自包含 release attestation
```

生产运行时只读取自包含的 release attestation，并校验当前实现指纹、认证摘要、
数量门槛和内部一致性。生产运行时不得重新打开 manifest、金样根目录或拆板后
DXF。

## 3. 唯一制造交接模型

新增不可变 `BoxDeliveryBatch`，作为 BOX 正式 writer 和正式 validator 的唯一
输入。它不允许从 `SplitAssembly`、原始 DXF 或监督金样补充语义。

每个 `BoxDeliveryPlateGroup` 至少包含：

- 稳定 `group_id`；
- 一个或两个物理角色；
- 交付数量；
- 规范化零件标签；
- 材质和板厚；
- 闭合外轮廓顶点；
- 已证明的圆孔中心和半径；
- 假设、来源图元和边缘 provenance；
- 制造语义 fingerprint。

四个物理角色仍必须完整存在。交付分组只在以下字段全部等价时合并：

- 同一板族（翼缘或腹板）；
- 板厚；
- 规范化外轮廓；
- 全部切孔几何。

因此两个对称且完全同形的物理实例可以合并为数量 2 的一个交付组；同形但孔不同
不得合并。物理角色与交付标签分开保存，避免把上/下、左/右的投影视角误当成
几何身份。

第一阶段正式交付只接受：

- 有效、闭合、正面积的直线段 Polygon 外轮廓；
- 可证明为圆的切孔；
- 完整来源追踪。

出现非圆内轮廓、无效 Polygon、来源不完整或无法稳定分组时 fail closed，不得由
writer 猜测或修复。

`to_split_assembly()` 保留为复核图和旧版辅助图适配器，但不再作为正式 REGION
writer 的输入。

## 4. 路由与授权矩阵

| 制造合同状态 | `require_auto_accept` | 正式 REGION | 复核候选 | 结果 |
|---|---:|---:|---:|---|
| `production_ready=true` | 任意 | 可按 `write_clean` 生成 | 不作为正式交付 | `auto_accepted` |
| `review_required=true` | `false` | 禁止 | 可按 `write_review` / `write_sheet` 生成 | `review_required` |
| `review_required=true` | `true` | 禁止 | 禁止 | 抛错且不留下本次产物 |
| reject/合同不完整 | 任意 | 禁止 | 禁止 | 抛错 |

正式路径：

```text
<output_root>\auto_accepted\<构件>_自动拆板_清洁1to1.dxf
<output_root>\auto_accepted\<构件>_自动拆板_报告.json
```

复核路径：

```text
<output_root>\review_required\<构件>\<构件>_复核候选1to1.dxf
<output_root>\review_required\<构件>\<构件>_图纸比例.dxf
<output_root>\review_required\<构件>\<构件>_复核_报告.json
```

`SplitResult.clean_path` 只表示正式生产文件。未认证运行即使
`write_clean=true`，该字段也必须为 `None`，且不得产生带“清洁”或
`auto_accepted` 含义的 DXF。

所有本次目标文件仍先写到同盘临时目录。只有 writer、DXF audit、重新打开、实体
类型、数量、几何、标签和报告序列化全部通过后，才原子提升到目标路径。

## 5. REGION 正式交付合同

正式生产 DXF 固定为：

- DXF R2007 / `AC1021`；
- `$INSUNITS=4`，单位毫米；
- 板外轮廓：`REGION`，图层 `PLATE_CUT`；
- 圆孔：独立 `REGION`，图层 `CUT_HOLE`；
- 零件标签：`TEXT`，图层 `PART_LABEL`；
- 文字样式：`SplitChinese`，字体 `simsun.ttc`；
- 不保留制造图层上的 `LINE`、`LWPOLYLINE`、`POLYLINE`、`CIRCLE` 或
  `ARC`；
- 不保留原图块、尺寸、中心线、十字线和辅助线。

REGION 由交接模型的精确轮廓独立构造。圆孔 REGION 的离散误差必须被 validator
量化约束，不能只比较实体数量。

正式 writer 返回不可变布局快照，正式 validator 使用同一
`BoxDeliveryBatch` 重新计算期望布局，并验证：

- audit 无错误；
- REGION 数量与交付组、切孔数量一致；
- 每个 REGION 只有一个平面面；
- 保存后的边界与期望边界在声明公差内一致；
- 标签文本、样式、位置与交付组一致；
- 标签点位于对应板材区域内且不落入孔；
- 无 legacy manufacturing curves；
- writer 返回布局与 validator 重算布局一致。

复核候选可以继续使用现有 LWPOLYLINE/CIRCLE 表达，但报告必须明确
`non_production_review_candidate=true`，不得伪装成正式生产文件。

## 6. 离线认证与运行时证明边界

新增轻量运行时模块负责：

- 计算生产实现 fingerprint；
- 定义 release attestation schema；
- 写入规范化认证摘要；
- 在不访问金样的情况下加载并验证 attestation。

生产 fingerprint 覆盖至少：

- BOX facts、metadata、geometry、projection、reconstruction、solver；
- manufacturing 和 delivery IR；
- REGION 构造、正式 writer、正式 validator；
- BOX pipeline 与相关项目依赖锁定文件；
- Python、ezdxf、shapely 运行时版本。

`box_supervision.py` 继续作为离线评估模块，负责发现、manifest、人工批准信息、
拆板后真值读取、20 对比较和评估报告。它调用运行时模块生成 release
attestation，但 `box_pipeline.py` 不得再导入 `box_supervision.py` 或
`reference_geometry.py`。

release attestation 只包含生产授权所需摘要：

- schema version；
- 创建时间；
- `passed=true`；
- 总对数、校准集数量、验收集数量；
- manifest fingerprint；
- gate fingerprint；
- production implementation fingerprint；
- 规范化 payload digest。

运行时检查 schema、类型、数量下限、`passed`、payload digest 和当前生产实现
fingerprint。它不信任任意调用方构造的普通字典。

该机制用于检测误用、漂移和过期认证，不宣称抵抗拥有本机文件写权限的恶意攻击。
若将来需要防篡改，应在独立发布系统中增加签名密钥和签名验证，而不是把密钥放入
本仓库。

现有 CLI 参数 `--box-supervised-gate-proof` 暂时保留为兼容别名，但帮助和报告
明确其文件已经是 release attestation；内部生产代码使用 release
attestation 命名。

## 7. 兼容性与非目标

- 不改动 BH pipeline。
- 不重写已通过的 BOX 多视图重建和 solver。
- 不引入运行时 fallback solver。
- 不把 v0.2.1 当作依赖或 vendor 代码。
- 不安装新依赖；使用项目现有 ezdxf 和 shapely。
- 不在本任务中实现任意形状的非圆内轮廓 REGION。
- 不承诺超出 20 对权威样例所覆盖制图分布的无限泛化。
- 不提交、不推送；除非用户另行明确要求。

## 8. 测试策略

实现遵循测试先行，至少新增以下测试组：

1. `test_box_delivery_ir_v1.py`
   - 四物理角色完整；
   - 等价几何合并且数量正确；
   - 同形不同孔不合并；
   - 非圆孔、缺 provenance、未授权合同 fail closed；
   - fingerprint 对顺序稳定、对语义变化敏感。
2. `test_box_region_delivery_v1.py`
   - 矩形、多边形和圆孔 REGION 保存回读；
   - 正式输出只含 REGION + PART_LABEL；
   - REGION 几何、数量、标签、单位和版本闭环；
   - LWPOLYLINE/CIRCLE 等 legacy 制造实体导致验证失败。
3. `test_box_release_attestation_v1.py`
   - 离线评估可生成自包含 attestation；
   - 移除临时 manifest/corpus 后运行时仍可加载；
   - schema、数量、摘要、代码指纹漂移全部 fail closed；
   - 生产模块静态禁止导入监督和真值模块。
4. 路由集成测试
   - 未认证运行只进入 `review_required`；
   - 未认证运行永不返回或写出 clean；
   - `require_auto_accept` 未满足时零本次产物；
   - 有效 attestation 才进入 `auto_accepted`；
   - 正式结果为 REGION 并通过保存后 validator。

## 9. 验收标准

完成必须同时满足：

- 新增和既有 BOX 单元/集成测试全部通过；
- 生产代码静态不导入 `box_supervision` 和 `reference_geometry`；
- 对 20 对只读金样重新执行离线认证，20/20 通过；
- 用新 attestation 对 20 个拆板前 DXF 批量正式交付；
- 每个正式 DXF 的 REGION 数量、孔数量、标签和保存回读均通过；
- 20 个正式 DXF 中 legacy manufacturing curve 数量为 0；
- 完整项目测试通过；
- 两个金样目录哈希在任务前后保持不变；
- `git diff` 只包含本任务相关实现、测试、规格和计划。
