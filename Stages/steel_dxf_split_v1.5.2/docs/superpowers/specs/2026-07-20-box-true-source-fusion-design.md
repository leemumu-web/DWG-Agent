# BOX 源码级融合设计

## 1. 决策

BOX 采用单一核心、单一数据流、单一生产路径：

- 以 `box-dxf-split v1.0.0` 的实际发布源码为冻结算法基线；
- 将项目 2 的 BOX 算法源码和测试纳入主项目，由主项目直接维护，不再作为外部后端调用；
- 按 BH 编译器的组织形式重新划分 BOX 阶段，但不改变项目 2 已解决的角色、方向、等价合并、孔归属和几何重建语义；
- 旧 BOX 算法不参与求解、不投票、不回退、不拼接结果；
- 旧 BOX 只提供已经验证且项目 2 尚未覆盖的外围工程能力。

这不是 `legacy + v2` 双后端，也不是把项目 2 MIR 降级成旧 `SplitAssembly` 后继续运行旧链路。

## 2. 权威边界

### 2.1 算法权威

`box-dxf-split v1.0.0` 是 BOX 领域算法的唯一基线。已核验的 tag 对象为
`1f55423a922e8ad4ba57342782ab294887c24359`，实际 commit 为
`5a2be1a82eb7235bcff62d97a13d2937f9ad026b`。不得再用 `v0.2.1`
或其他 commit 代替最终版本。

项目 2 已有语义直接保留：

- DXF 解码、源事实与 provenance；
- 局部视图坐标系和方向语义；
- metadata、course graph 与视图归属；
- H/B 视图判定和投影 lowering；
- web/flange 候选生成；
- 完整组件假设与全局装配求解；
- 四块制造板的最终求解；
- 孔归属、内轮廓和制造几何；
- 等价结果合并；
- `ManufacturingIR`、`ProofReport` 和质量路由；
- 确定性写出与保存后回读验证。

不得用旧 BOX 的左右腹板、上下翼板假设重新解释或覆盖这些结果。

### 2.2 样例权威

以下目录只读，是算法冲突和最终验收的最高证据：

- `D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf`
- `D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf`

文件名、人工描述、CAD Agent/DWG 规则和旧算法结果只能辅助解释，不能覆盖成对 DXF 的几何证据。

### 2.3 外层架构

遵循《架构设计.txt》的拆板集成边界：

1. 先分类并抽取 BH/BOX 语义；
2. 自动拆板使用固定、可复现的编译阶段；
3. 结果验证与拆板求解解耦；
4. 只有验证通过的 DXF 才进入正式输出；
5. 证据不足时 fail closed，转人工处理。

## 3. 目标结构

BOX 在主项目内成为与 BH 对称的编译器包：

```text
steel_dxf_split/
  box/
    contracts.py
    frontend.py
    analysis.py
    solve.py
    manufacturing.py
    validation.py
    delivery.py
    compiler.py
```

这是职责分层，不是重写算法。项目 2 的成熟模块优先原样迁入对应层；只在入口、结果契约和跨层依赖处做最小调整。

### Pass 1：Frontend

输入 DXF，完成 recover/audit、块展开、实体归一化、稳定 provenance 和源文档指纹，产出不可变 `BoxSourceIR`。

### Pass 2：Analysis

基于项目 2 的逻辑构建 view frame、metadata、course graph、视图归属和投影关系，产出 `BoxAnalysisIR`。

### Pass 3：Solve

运行项目 2 的候选生成、web/flange lowering、孔归属、完整组件假设和全局装配搜索，产出完整求解结果。不存在旧 solver fallback。

### Pass 4：Manufacturing

将唯一获胜假设编译为项目 2 的制造 IR，执行等价合并、几何规范化、证明生成和质量判定。

### Pass 5：Validation

独立验证制造 IR 的完整性、一致性和可制造性；正式 DXF 保存后重新打开，并与制造 IR 对照。验证器不得调用 solver，也不得修改结果。

### Pass 6：Delivery

负责 staging、原子提升、回滚、release attestation、批处理隔离和结构化报告。它只消费已冻结并验证的制造 IR，不解释领域几何。

统一入口：

```python
compile_box(
    input_path: Path,
    *,
    config: BoxCompileConfig,
) -> BoxCompilationResult
```

`BoxCompilationResult` 直接包含制造 IR 指纹、证明、质量路由、验证结果、产物和报告；不再暴露 `backend=v2|legacy`。

## 4. 能力归属

| 能力 | 最终归属 | 处理方式 |
|---|---|---|
| BOX 识别、视图、角色、几何、孔、装配求解 | 项目 2 | 整体保留 |
| 制造 IR、证明、等价合并 | 项目 2 | 整体保留 |
| 领域 DXF writer 与保存后几何校验 | 项目 2 优先 | 缺口只在 MIR 后补齐 |
| BH/BOX 统一型号路由 | 主项目 | 保留并改为单 BOX 入口 |
| staging、原子提升、失败回滚 | 旧主项目外围 | 保留 |
| release attestation、golden firewall | 旧主项目外围 | 保留 |
| 批处理进程隔离、超时、零产物失败 | 旧主项目外围 | 保留 |
| 结构化报告和人工复核路由 | 两者收敛 | 合并为一个契约 |
| 旧 metadata/solver/reconstruction | 无 | 从生产路径删除 |
| MIR 到旧 `SplitAssembly` 的适配 | 无 | 删除 |
| `v2/legacy` 后端选择与 fallback | 无 | 删除 |

旧 BOX 若声称还有算法能力，必须先通过成对 DXF 差分证明项目 2 的确定缺口。确认缺口后，也应按项目 2 的 IR 和证明体系实现一个新 pass，而不是恢复旧角色模型或把两个结果拼接。

v1.0.0 的正式制造实体固定为闭合 `LWPOLYLINE`、原生 `CIRCLE` 和
`TEXT`，保存后验证必须拒绝制造层上的 `REGION`、开放轮廓和辅助线。

## 5. 迁移方式

### 阶段 A：冻结项目 2 最终基线

- 获取 `v1.0.0` release 的实际源码；
- 记录 tag、commit、源码指纹、依赖锁和完整测试结果；
- 先在原项目环境证明其基线可复现。

### 阶段 B：源码与测试迁入

- 将项目 2 的核心源码迁入 `steel_dxf_split.box`；
- 同步迁入其单元测试、性质测试和样例验证；
- 先只改 import/package 边界，保持算法行为不变；
- 每批迁移都与原 `v1.0.0` 做结果指纹对照。

### 阶段 C：接入主项目外围

- 接入统一 BH/BOX 路由；
- 在制造 IR 之后接入 staging、认证、报告和批处理隔离；
- writer/validator 功能去重，保留唯一生产实现；
- 不生成旧 `SplitAssembly`，不调用旧 BOX 求解链。

### 阶段 D：切换与清理

- 将 `pipeline.py` 的 BOX 分支切到唯一 `compile_box()`；
- 删除 `box_backend` 配置、`box_v2_backend.py`、`box_v2_pipeline.py` 和相关适配测试；
- 删除生产运行时对独立 `box-dxf-split` 包的依赖；
- 旧 BOX 算法只在迁移验证期间作为只读对照，验收后从运行路径和发布包移除。

## 6. 失败与质量策略

- profile 冲突、源事实不完整、搜索不完整、多个不可区分假设、制造 IR 不完整或保存后验证不一致，一律不得正式输出；
- `auto_accept` 必须同时通过算法证明、独立验证和 release attestation；
- `review_required` 可以生成明确标记的复核资料，但不能伪装成生产产物；
- `rejected` 不得留下本次正式产物；
- 超时和异常必须保持零正式产物，并记录可诊断报告。

## 7. 验收

BOX 源码级融合完成必须同时满足：

1. `v1.0.0` 原有完整测试在主项目内通过；
2. 项目 2 原有样例的 disposition、制造 IR 和写出结果保持基线等价；
3. 权威 BOX 拆板前后样例全部通过几何、孔、数量、板件集合和保存后回读对照；
4. 权威输入目录任务前后内容哈希不变；
5. 生产调用图中不存在 legacy BOX solver、双后端、自动 fallback 或 `SplitAssembly` 降级桥；
6. 故障注入验证 staging、回滚、超时和零产物语义；
7. BOX 全套测试、项目全套回归、lint 和类型检查通过；
8. 后续落到最新 BH 主干时，BH 验收保持不变。

## 8. 明确删除的旧方案

`2026-07-20-box-v2-core-fusion-design.md` 所描述的“固定外部依赖 + 唯一适配器 + legacy 显式回退”不是最终设计。它只可作为错误路径记录，不得继续指导实现。

本文件是 BOX 融合的当前权威设计。
