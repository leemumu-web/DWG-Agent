# BH v1.5.2 + Project2 BOX 简化 Worker 融合计划

状态：实施中，取代同日的 `bh-box-domain-authority-integration` 复杂分层方案。

## 最终边界

- 项目根包的 BH 是完整 `steel-dxf-split v1.5.2`，不是旧 BH，也不是兼容壳。
- BOX 是现有已融合的 `steel_dxf_split.box` Project2 v1.0.0 核心。
- 公共入口只扫描 DXF 型材证据，唯一识别为 BH 或 BOX 后，直接调用对应原生核心。
- 不建立 `application/`、adapter、backend 注册表、双跑、投票、结果拼接或 fallback。
- BH 与 BOX 分别保留自己的 SourceIR、ManufacturingIR、ProofReport、writer 和保存后验证。
- 最终运行环境是 Linux Worker；Windows 只做不依赖 POSIX 进程组、Linux CJK 字体和目录刷盘的开发验证。

## 实现结构

```text
steel_dxf_split/
├── pipeline.py            # 薄判型与唯一分发
├── cli.py                 # 单文件入口
├── batch_cli.py           # Linux 隔离批处理入口
├── profile_detection.py   # BH/BOX 唯一判型
├── bh_*.py                # 完整 BH v1.5.2 主体
├── layered_*.py           # BH v1.5.2 原生辅助能力
├── weld_allowance*.py     # BH v1.5.2 原生焊接余量能力
└── box/                   # 已融合 Project2 BOX 原生核心
```

## 验收口径

1. BH v1.5.2 来源校验无缺失、无额外语义改写；仅包入口、单文件入口、批入口和薄分发属于集成接缝。
2. 20 张 BH 冻结源图全部判型为 BH，并保持 v1.5.2 原生证明结果。
3. 项目2的 30 张 BOX 源图全部判型为 BOX，并由 Project2 核心完成证明。
4. 权威 BOX 前后语料仍是算法验收依据；不把 fail-closed 样本伪装成自动通过。
5. Linux 上补跑预览、POSIX 进程隔离、原子目录刷盘和批量发布门。
