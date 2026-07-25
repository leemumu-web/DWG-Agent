# BOX v2 核心融合设计

> **已废弃（2026-07-21）：** 本文记录“外部 v0.2.1 适配器 + legacy 回退”的错误
> 路径，不再作为实现依据。当前设计见
> `2026-07-20-box-true-source-fusion-design.md`。

## 1. 决策

BOX 继续保留原项目已经验证的交付与发布能力，但制造语义核心切换为
`box-dxf-split v0.2.1`。

最终组合为：

```text
主项目唯一型材路由
→ 显式 BOX source contract
→ box-dxf-split v0.2.1 SourceIR / metadata / joint solve / MIR / ProofReport
→ 主项目 release attestation
→ 主项目同盘 staging 与原子提升
  ├─ 有效单图证明 + 有效版本认证 → auto_accepted REGION
  └─ 单图可解但版本未认证       → review_required
```

本设计替代
`docs/superpowers/specs/2026-07-19-box-delivery-and-release-certification-design.md`
第 1 节和第 7 节中“外部算法仅作参考、不作为依赖”的决定。调整依据是用户已经明确
选择“新算法做核心、旧项目贡献成熟外围能力”。

## 2. 目标

- 项目 2 的 30 张 DXF 继续保持 30/30 `auto_accept` 单图证明；
- 权威 20 对样例继续保持制造几何、孔、数量和板族 20/20；
- 主项目 BOX 正式路径不再调用 legacy metadata/solver；
- 未获得新实现 release attestation 时不得产生正式 clean；
- BH 行为和接口保持不变；
- 保留 legacy BOX 作为显式回退，迁移稳定后再删除。

## 3. 非目标

- 本阶段不按人工答案硬编码 4 个侧别标签冲突；
- 本阶段不把拆板后 DXF 引入运行时；
- 本阶段不宣称已经识别任意形状的非圆孔；
- 不让新旧 solver 投票；
- 不提交、不推送；
- 不修改、移动或重命名两个权威金样目录。

## 4. 依赖方式

主项目通过固定 Git commit 的 Python 依赖使用新核心：

```text
box-dxf-split @
git+https://github.com/Creeken-Harrans/box-dxf-split.git@b7b47f33cec1b8c2ae881badc4400cd57d136d2d
```

不复制上游 7,500 余行源码。这样可以：

- 保留上游模块和测试的独立性；
- 精确记录融合使用的 commit；
- 避免两个仓库出现无法同步的源码副本；
- 让 release implementation fingerprint 覆盖实际安装的新核心文件。

当前上游没有 `LICENSE` 文件。内部技术融合可以继续，但任何对外分发或商业发布必须
先由代码权利人补充明确许可证或书面授权。本设计不推定 GitHub 公共可见等于获得再分发
许可。

## 5. 深模块接缝

新增 `steel_dxf_split.box_v2_backend`，它是主项目与上游核心之间的唯一适配器。调用方
只知道：

```python
compile_box_v2(
    input_path: Path,
    *,
    source_contract: BoxSourceContract,
) -> BoxV2Compilation
```

`BoxV2Compilation` 暴露稳定摘要、单图 disposition、MIR fingerprint、报告字典和
legacy review 适配结果；上游 `SourceDocumentIR`、候选类型和内部求解函数不泄漏到
公共 `pipeline.py`。

正式 REGION 的写出和验证仍由适配器调用上游 MIR-only writer/validator。复核 1:1
和图纸比例输出由适配器把冻结 MIR 降级为主项目 `SplitAssembly` 后交给既有外围
writer。这个降级结果不得用于生产授权或正式 REGION。

## 6. 路由

`detect_profile_family()` 必须先收集全部 BOX/BH profile 事实：

- 只有 BOX → `"BOX"`；
- 只有 BH → `"BH"`；
- 两者都没有 → `None`；
- 同时出现 BOX 与 BH → 结构化冲突错误。

BOX 分派增加：

```text
box_backend = v2 | legacy
```

默认值为 `v2`。`legacy` 只用于显式迁移回退，不可在 v2 失败后自动 fallback。

选择 `v2` 时必须提供 `BoxSourceContract`。CLI 通过以下参数显式授权：

```text
--authorize-tekla-box-single-part-profile project_tekla_box_dxf_v1
```

## 7. 双层授权

单图 `ProofReport` 和版本 `ReleaseAttestation` 分离：

| 单图 disposition | release attestation | 外层路由 |
|---|---|---|
| `auto_accept` | 有效 | `auto_accepted` |
| `auto_accept` | 缺失 | `review_required` |
| `review_required` | 任意 | `review_required` |
| `rejected` | 任意 | 拒绝且无产物 |

`require_auto_accept=true` 要求两层同时通过，否则抛错且不提升任何本次产物。

融合会改变 production implementation fingerprint。旧 attestation 必须自然失效，
不得复用或人工改写。

## 8. 输出

正式生产：

- 上游 `BOX-MIR-1.0` 是唯一几何真相；
- 上游 writer 生成 `PLATE_CUT/REGION`、`CUT_HOLE/REGION` 和
  `PART_LABEL/TEXT`；
- 上游 validator 保存后重新打开并比较 MIR；
- 主项目只负责 staging、路径、报告和原子提升。

复核输出：

- 可以使用主项目既有 LWPOLYLINE/CIRCLE review writer；
- `SplitAssembly` 只由冻结 MIR 适配；
- 不运行 legacy solver；
- 报告必须写明 `non_production_review_candidate=true`。

## 9. 报告

新报告 schema 为 `BOX-COMPILATION-REPORT-3.0`，至少包含：

- `backend.id = box-dxf-split`；
- `backend.version = 0.2.1`；
- `backend.commit = b7b47f33cec1b8c2ae881badc4400cd57d136d2d`；
- source contract；
- metadata 摘要；
- `ProofReport`；
- search status 和 hypothesis count；
- MIR fingerprint 与 validation；
- release attestation 摘要；
- 外层 automation route；
- 保存后 DXF validation；
- `ground_truth_used_for_decision=false`。

## 10. 已知边界

### 10.1 侧别标签

权威 20 对中有 4/63 输出组的人工上/下名称与新核心角色相反，但制造几何均通过。
内部物理角色和展示标签必须保持可分离。本阶段保持 v0.2.1 行为并显式报告，不按样本名
修正。

### 10.2 搜索预算

v0.2.1 有 batch 进程超时，但内部候选预算仍不完整。本次主项目至少保留每文件进程
超时和零产物语义；内部候选/配对/假设预算在上游后续版本实现，预算截断不得设置
`search_complete=true`。

### 10.3 异形孔

上游 MIR/writer 支持 `inner_contours`，当前 opening solver 主要识别 Bolt CIRCLE。
因此只能宣称“输出适配已具备”，不能宣称“异形孔识别已完整”。

## 11. 验收

1. 新适配器单元测试和保存回读测试通过；
2. 项目 2：30/30 单图 `auto_accept`，0 failed；
3. 权威 20 对：制造几何、孔、数量、板族 20/20；
4. 20 张正式 REGION 使用新实现 attestation 后全部保存回读；
5. legacy BOX 失败不得阻断 v2；
6. 混合 BOX/BH profile 稳定拒绝；
7. 无 source contract 稳定拒绝且无产物；
8. BH 全套回归不变；
9. 两个权威目录任务前后 SHA-256、长度和修改时间不变。
