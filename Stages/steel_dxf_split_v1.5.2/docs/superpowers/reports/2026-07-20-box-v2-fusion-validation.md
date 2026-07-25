# BOX v2 核心融合验收报告

> **历史结果，已被替代（2026-07-21）：** 本报告验证的是 v0.2.1 外部后端与
> legacy 回退方案。当前结果见 `2026-07-21-box-v1-fusion-acceptance.json`。

## 结论

本次融合验收通过。主项目现在以 `box-dxf-split v0.2.1` 的制造语义算法为
BOX 默认核心；原 BOX 算法不再参与默认路径的识别、重建或求解，只保留为显式
`legacy` 迁移回退，以及审阅绘图、原子提升、报告和发布证明等外围能力。

## 验收范围

- 融合工作树：`D:\Dev\Projects\dxf agent\worktrees\box-completion`
- 上游固定版本：`box-dxf-split v0.2.1`
- 上游固定提交：`b7b47f33cec1b8c2ae881badc4400cd57d136d2d`
- 权威拆板前语料：`D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf`
- 权威拆板后语料：`D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf`
- 项目2独立语料：`D:\DevData\项目2_BOX_dxf`
- 机器报告：`D:\AppDataRelocated\Temp\box-v2-fusion-xwyagqr3\box-v2-fusion-validation.json`
- 本次生产输出：`D:\AppDataRelocated\Temp\box-v2-fusion-xwyagqr3\production`
- 本次发布证明：`D:\AppDataRelocated\Temp\box-v2-fusion-xwyagqr3\release\box-release-attestation.json`

所有 DXF 副本、生产输出和发布证明均写入新建的系统临时目录。权威语料只读，
运行前后分别记录 SHA-256、文件长度和 `mtime_ns`。

## 结果

| 验收项 | 结果 |
|---|---:|
| 权威金样本配对 | 20/20 |
| 权威金样本 v2 单图证明 | 20/20 `auto_accept` |
| 曲线级制造语义对比 | 20/20 通过 |
| 项目2独立输入 | 30/30 `auto_accept` |
| 融合主入口生产 REGION | 20/20 |
| 保存后 REGION 复开验证 | 20/20 |
| 失败或拒绝 | 0 |
| 权威目录文件变化 | 0 |

生产闭环不是直接调用旧 writer。每个生产文件均经过：

1. 显式 Tekla BOX 单构件来源契约；
2. v2 Source IR、元数据、视图搜索、证明和 Manufacturing IR；
3. 单图 `auto_accept`；
4. 与当前实现指纹绑定的 20 对发布证明；
5. v2 MIR-only REGION writer；
6. 保存后复开、拓扑和 writer closure 验证；
7. 同盘暂存完成后的原子提升。

## 指纹

- production implementation fingerprint：
  `1ae9429bb15983a161db309722fe7e58dc859085a38a115199268d3147234fd1`
- manifest fingerprint：
  `df188e5b3113dcb8045fbc441c96d5e8304d8afbaf6898398e35ec7c3dc7a58d`
- gate fingerprint：
  `ad0f901c3c47f367ef883a63453ea32d3b93972eb6fbfc8ac4f5b5f6c1eb4d2e`

production implementation fingerprint 同时覆盖主项目融合模块、依赖锁、运行时版本
及上游实际生产核心源码；任何一侧漂移都会使本次发布证明失效。

## 标签诊断

曲线比较器记录了 4 条精确侧名差异：

| 构件 | 族 | 人工标签 | v2 输出标签 |
|---|---|---|---|
| `2b1-cb-86` | web | `下腹` | `p=2b1-cb-86上腹` |
| `2b1-cb-86` | web | `上腹` | `p=2b1-cb-86下腹` |
| `h-9-cb-133` | flange | `下翼` | `p=h-9-cb-133上翼` |
| `h-9-cb-133` | flange | `上翼` | `p=h-9-cb-133下翼` |

这些差异是成对等价板的局部“上/下”名称分配差异。对应的制造轮廓、曲线、
孔位、孔径、数量、构件号和腹板/翼缘族均通过。它们不降低制造验收结果，但继续
作为可见诊断保留，不能被描述成“人工侧名完全一致”。

## 运行边界

- 默认 `box_backend=v2`，失败时不自动回退旧算法。
- `legacy` 只能由调用方显式选择。
- 缺少来源契约时，v2 在读取和产出前拒绝。
- 单图证明通过但没有有效发布证明时，只能进入复核路径。
- `require_auto_accept` 下任一门控缺失都会零产出失败。
- 生产运行时不读取拆板后 DXF；人工拆板后文件只在离线验收阶段读取。

## 剩余边界

- 上游仓库当前没有 `LICENSE` 文件。本次技术融合可用于内部验证，但任何对外分发、
  商业发布或向第三方提供包含该依赖的程序前，必须取得代码权利人的明确许可证或
  书面授权。
- v0.2.1 的内部候选预算仍不完整。批处理层保留每文件超时和零产出语义；超时不得
  自动切换到 legacy。
- 上游 MIR/生产 writer 支持 `inner_contours`，但当前 opening solver 主要覆盖
  Bolt CIRCLE，且主项目复核适配器会拒绝带 inner contour 的候选。
