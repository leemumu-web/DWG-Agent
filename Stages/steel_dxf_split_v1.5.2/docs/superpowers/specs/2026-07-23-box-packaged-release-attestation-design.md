# BOX 内置发布认证设计

## 目标

将已经通过 Linux 发布门的 BOX release attestation 作为 wheel 资源发布，使正式 BH/BOX Worker 只依赖一个 wheel 即可完成生产授权，不再要求部署人员额外携带认证 JSON。

## 范围

- 不修改 BH 或 BOX 制造几何、判型、证明、质量门和 DXF writer。
- 保留 `--box-release-attestation`，仅作为显式覆盖入口，便于审计、测试和重新签发验证。
- 默认未传路径时，从 `steel_dxf_split/release_evidence/box_release_attestation.json` 读取认证。
- 内置认证缺失、损坏或与当前实现指纹不匹配时失败关闭，不降级到无认证生产。
- `pyproject.toml` 已发布 `release_evidence/*.json`，无需新增包装机制。

## 数据流

```text
统一 Worker 判定 BOX
→ compile_box
→ 显式 attestation 路径（若提供）
   否则读取 wheel 内置 attestation
→ 校验摘要、20 对数量、核心版本和当前实现指纹
→ Project2 BOX 核心
→ ProofReport + Quality Gate
→ auto_accepted / review_required / rejected
```

## 发布流程

代码变更会改变 BOX 生产实现指纹，因此先完成加载逻辑，再用只读 20 对权威语料重新执行发布门并生成新的内置认证。随后构建最终 wheel，在隔离安装环境确认不传 `--box-release-attestation` 也能完成 BOX 自动生产，同时显式错误认证仍会失败关闭。

## 验收标准

- 内置认证能从源码布局及安装后的 wheel 布局加载并验证。
- BOX `require_auto_accept` 不再要求外部认证路径。
- 显式路径仍覆盖内置资源。
- wheel 内容包含内置认证。
- BH 行为与输出不变。
- BOX 权威 20 对发布门、统一入口、批处理事务及完整测试通过。
