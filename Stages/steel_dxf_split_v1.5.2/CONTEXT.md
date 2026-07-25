# 项目领域上下文

## 正式程序与唯一入口

正式拆板程序位于本仓库，唯一公开入口是：

```text
steel-dxf-split
```

执行链为：

```text
steel-dxf-split
→ cli.main：只快照输入目录
→ pipeline.split_dxf：单图判型与一次领域拆板
  ├─ BH：BH v1.5.2 原生核心
  └─ BOX：Project2 BOX v1.0.0 核心
→ 同一基础结果派生 normal / weld_allowance
→ 成对验证
→ auto_accepted / manual_review 任务目录原子发布
```

`src/steel_dxf_split/pipeline.py` 是唯一的单图分派边界。直接调用领域编译器仅是内部验证 seam，不是生产入口。BOX 包保留领域算法、证明、验证和交付能力；顶层只负责判型、授权、结果归一化和交付，不能新增第二套公开入口、backend、fallback 或双算法投票。

## 领域权威

BH 算法权威为 `steel-dxf-split v1.5.2`。当前来源核验结果为：

```text
48 个逐字节一致文件
5 个哈希锁定的 Worker 集成适配
8 个哈希锁定的已声明领域补丁
0 缺失 / 0 意外文件 / 0 非法适配
```

BOX 算法权威为 `box-dxf-split v1.0.0`：

```text
tag = v1.0.0
commit = 5a2be1a82eb7235bcff62d97a13d2937f9ad026b
19 个逐字节一致核心文件
7 个哈希锁定的制造语义补丁
1 个哈希锁定的包入口适配
3 个明确退役的旧编排文件
0 缺失 / 0 意外文件 / 0 未登记改写
```

Project2 的视图、角色、等价合并、孔归属、几何重建、`ManufacturingIR`、`ProofReport`、writer 和 saved-DXF validator 都属于 BOX 领域权威。

## 统一结果契约

BH 和 BOX 的原生报告保持不变；顶层 `SplitResult` 统一提供：

```text
family
automation_route = auto_accepted | manual_review | rejected
native_automation_route
disposition
production_ready
proof_disposition
diagnostic_codes
previews
timing
```

BH 原生 `production` 只在顶层边界映射为 `auto_accepted`。领域差异只保留在原生报告及各自的 DXF/预览验证器中。

## 单图事务

单图的 DXF、报告、源图副本和预览必须全部验证后一起提升；提升中途失败时撤回新文件并恢复该图旧文件。已经完成的其他图纸不因后续图失败而回滚，整批完成屏障由上层任务系统负责。

## BOX 来源授权与发布认证

BOX source contract 由调用方提供，不能从图层名或块名猜测：

```text
source_system = tekla_structures
drawing_kind = single_part_drawing
member_family = welded_box
export_profile = project_tekla_box_dxf_v1
```

`auto_accepted` 同时要求当前单图证明为 `auto_accept`，并且 release attestation 与当前生产实现指纹一致。正式 attestation 随 wheel 内置；调用方不提供路径时读取 `release_evidence/box_release_attestation.json`，显式路径只作为审计或测试覆盖。内置认证缺失、损坏或漂移时必须失败关闸。架构文件变化会主动使旧 attestation 失效；未完成新 Linux 发布门前只能把新 wheel 视为候选，不得沿用旧认证冒充正式签发。

## 权威语料

BOX 制造几何的最终真值来自以下只读目录：

```text
D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf
D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf
```

生产运行不得读取拆板后 DXF、样本名白名单或离线比较结果；这些内容只进入 `tools/compare_box_corpus.py` 和 `scripts/verify_box_v1_fusion.py` 的离线验收链。
