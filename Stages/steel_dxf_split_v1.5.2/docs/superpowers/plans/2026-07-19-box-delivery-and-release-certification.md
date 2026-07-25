# BOX 制造交接、REGION 交付与离线认证实施计划

> **历史记录，已废弃（2026-07-21）：** 本计划基于旧 BOX 算法和 REGION
> 交付，不得继续执行。当前计划为 `2026-07-21-box-v1-source-fusion.md`。

> 执行约束：在 `D:\Dev\Projects\dxf agent\worktrees\box-completion` 内测试先行；
> 不修改金样目录；不复制无许可证外部代码；不提交或推送。

**目标：** 在保留现有 BOX 多视图重建算法的前提下，一次完成规范化制造交接、
强生产/复核路由、REGION 正式交付和离线 release attestation。

**架构：** `BoxManufacturingIR` 先转换为不可变 `BoxDeliveryBatch`。正式 writer
和正式 validator 只消费该交接模型。生产 pipeline 只加载自包含 release
attestation，不导入监督样例模块；监督 CLI 只在线下读取权威前后样例并生成
attestation。

**技术栈：** Python 3.12、ezdxf 1.4.x、Shapely 2.1.x、pytest。

---

## 任务 1：冻结基线与加入制造交接失败测试

**文件：**

- 新增：`tests/test_box_delivery_ir_v1.py`
- 新增：`src/steel_dxf_split/box_delivery_ir.py`
- 修改：`src/steel_dxf_split/box_manufacturing.py`

1. 记录当前 `git status`、BOX 测试结果和金样文件哈希清单。
2. 编写测试证明四物理角色、等价合并、孔差异、provenance 和 fingerprint 合同。
3. 单独运行新测试，确认因模块/行为缺失而失败。
4. 实现最小不可变 delivery dataclass、圆孔规范化、分组和 fingerprint。
5. 运行新测试以及 `test_box_manufacturing_v2.py`，直到通过。

## 任务 2：加入 release attestation 失败测试

**文件：**

- 新增：`tests/test_box_release_attestation_v1.py`
- 新增：`src/steel_dxf_split/box_release.py`
- 修改：`src/steel_dxf_split/box_supervision.py`
- 修改：`src/steel_dxf_split/box_supervision_cli.py`

1. 编写测试，要求 attestation 自包含、实现漂移失效、摘要篡改失效、数量不足失效。
2. 加入静态架构测试，禁止生产模块导入 `box_supervision` / `reference_geometry`。
3. 运行新测试，确认旧 proof 因依赖 manifest/corpus 而失败。
4. 实现生产 fingerprint、attestation schema、原子写入和运行时 loader。
5. 让离线监督评估负责生成 attestation，并保留旧函数名作为离线兼容入口。
6. 运行 release、supervision、supervision CLI 测试直到通过。

## 任务 3：加入 REGION 构造与闭环失败测试

**文件：**

- 新增：`tests/test_box_region_delivery_v1.py`
- 新增：`src/steel_dxf_split/box_region.py`
- 新增：`src/steel_dxf_split/box_delivery_writer.py`
- 新增：`src/steel_dxf_split/box_delivery_validator.py`

1. 编写 REGION 矩形、多边形、圆孔保存回读测试。
2. 编写正式输出实体白名单、标签样式、几何闭环和 legacy 曲线拒绝测试。
3. 运行新测试，确认缺少实现而失败。
4. 使用 ezdxf ACIS 公共能力独立实现二维面 REGION 构造和边界读取。
5. 实现仅接收 `BoxDeliveryBatch` 的正式布局和 writer。
6. 实现重新打开 DXF 的正式 validator。
7. 运行新测试，直到 REGION audit、几何和标签闭环全部通过。

## 任务 4：切换 BOX pipeline 强路由

**文件：**

- 修改：`src/steel_dxf_split/box_pipeline.py`
- 修改：`src/steel_dxf_split/pipeline.py`
- 修改：`src/steel_dxf_split/cli.py`
- 修改：`src/steel_dxf_split/batch_cli.py`
- 修改：`tests/test_box_gate_integration_v1.py`
- 修改：`tests/test_box_atomic_pipeline_v1.py`
- 修改：`tests/test_box_split_v04.py`

1. 先修改路由集成测试：未认证无 clean、只进 review；有效 attestation 才进 production。
2. 运行路由测试，确认旧 pipeline 失败。
3. 把生产 import 切到 `box_release`、delivery IR、正式 writer 和正式 validator。
4. 依据制造合同选择 `auto_accepted` 或 `review_required/<构件>`。
5. 保持本次批次的同盘 staging、保存回读、报告写入和原子提升。
6. 更新 CLI 兼容参数说明和报告字段。
7. 运行路由、原子性和 BOX split 测试直到通过。

## 任务 5：回归与权威金样认证

**文件：**

- 可能修改：上述测试和实现文件中的最小缺陷修复
- 新增：`docs/superpowers/reports/2026-07-19-box-delivery-certification.md`

1. 运行所有 BOX 测试。
2. 运行完整项目测试，修复本任务引入的回归。
3. 对 20 对权威金样执行离线 verify，生成临时 release attestation。
4. 用该 attestation 对 20 个拆板前 DXF 生成临时正式输出。
5. 汇总每个输出的板 REGION、孔 REGION、标签、legacy 曲线和 audit 结果。
6. 复核金样前后哈希清单未变化。
7. 写中文验收报告，记录证明范围、限制和剩余风险。
8. 检查 `git status` / `git diff --check`，确认没有无关改动；不提交、不推送。
