# BOX 项目 2 单内核压缩融合验证报告

## 结论

本次一小时压缩范围已经完成：BOX 生产路径继续以 `box-dxf-split v1.0.0` 为唯一算法内核，旧主项目 BOX 没有恢复为第二套求解器。旧算法中项目 2 尚未显式具备的输入资源预算，已压缩为项目 2 SourceIR 与 analysis/solve 之间的统一失败关闭门。

项目 2 的视图、投影、Assembly、Proofs、ManufacturingIR、孔归属、writer 和 saved-DXF validator 没有被旧角色语义重新解释，也没有 legacy fallback、投票或结果拼接。

## 实际修改

- `src/steel_dxf_split/box/contracts.py`
  - 新增不可变 `BoxSourceLimits`；
  - 默认上限来自旧 BOX 前端的已验证安全预算：200,000 个实体、50,000 个文字实体、单实体 20,000 个点、16 层块深度、绝对坐标 `1.0e9`。
- `src/steel_dxf_split/box/frontend.py`
  - 新增稳定 `BoxSourceLimitError.reason_code`；
  - 对项目 2 已生成的不可变 SourceIR 检查实体数、文字数、点数、块深度和坐标有限性/范围；
  - 检查通过后返回同一个 SourceIR，不改几何、来源 ID 或指纹。
- `src/steel_dxf_split/box/compiler.py`
  - `BoxCompileConfig` 和 `compile_box_core()` 增加 `source_limits`；
  - 预算只传到唯一 `run_frontend()`，未触碰 analysis、solve、制造、writer 或 validator。
- `tests/test_box_compressed_capabilities.py`
  - 新增 13 个资源预算和唯一入口传播测试。
- `tests/box_v1/test_compiler_passes.py`
  - pass-order 监视器透明转发 `**kwargs`；原断言未放松。

## 旧能力压缩映射

| 旧能力 | 处理结果 | 证据 |
|---|---|---|
| 输入规模、点数、块深度、坐标预算 | 新增最小补丁 | 13 个新测试覆盖契约、五类失败码和编译入口零产物失败 |
| Source provenance、指纹、输入不变性 | 项目 2 已覆盖 | SourceIR 测试、30 文件来源核验、两套只读语料哈希 |
| 非等价歧义与搜索失败关闭 | 项目 2 已覆盖 | Assembly、Proofs、单内核契约测试 |
| 孔去重、唯一归属、不复制到其他板 | 项目 2 已覆盖 | openings、Assembly 和 saved-DXF 测试 |
| 保存后回读、临时写出和原子提升 | 项目 2 已覆盖 | writer、pipeline、atomic integration 测试 |
| station track、end chain、developed rail、旧实例配对 | 拒绝/延期吸收 | 权威 20 对和项目 2 的 30 文件均未证明存在缺口；不为保留旧代码而复制实现 |
| 旧 metadata/solver/reconstruction/writer | 拒绝吸收 | 生产调用图不需要它们；恢复会形成双内核或语义覆盖 |

## RED-GREEN 证据

1. RED：测试因 `BoxSourceLimits` 不存在而 ImportError；GREEN：预算契约 7/7 通过。
2. RED：测试因 `BoxSourceLimitError`/`limits` API 不存在而 ImportError；GREEN：五类 SourceIR 预算测试加入后 12/12 通过。
3. RED：`BoxCompileConfig` 拒绝 `source_limits`；GREEN：唯一编译入口传播完成后 13/13 通过。
4. 回归首次发现 pass-order 测试监视器不能转发关键字参数；只修复测试监视器后，入口与原子交付回归 16/16 通过。

## 定向与完整测试

- 修改前窄基线：23/23 通过。
- SourceIR 与来源测试：7/7 通过。
- 快速单内核、Proofs、孔归属和 firewall 契约：20/20 通过。
- 关键 writer 与保存后失败关闭：6/6 通过。
- 新增测试与 compiler pass：16/16 通过。
- 完整 BOX 回归：321 collected，310 passed，11 skipped，0 failed。
  - 11 条 skip 是项目 2 原有的 Windows/POSIX、字体或可选语料条件边界；没有隐藏失败。
- 定向 Ruff：通过。
- 定向 Mypy：`Success: no issues found in 5 source files`。
- `py_compile`：通过。
- `git diff --check`：通过；仅输出工作树既有文件的 LF/CRLF 提示，没有 whitespace error。

一次不限制依赖遍历的诊断 Mypy 共报告 82 项：本次新增前端有 1 项局部变量类型推断问题，已通过变量改名修复；其余 81 项来自未修改的 BH/共享依赖。没有在本次 BOX 压缩范围内修改这些旧债务，也不宣称全仓 Mypy 通过。

## 权威前后样例

命令读取：

- `D:\DevData\BOX拆板前后数据\BOX_拆板前_dxf`
- `D:\DevData\BOX拆板前后数据\BOX_拆板后_dxf`

实测结果：

```text
schema = BOX-V1-FUSION-ACCEPTANCE-1.0
sample_count = 20
passed = 20
failed = 0
all_passed = true
inputs_unchanged = true
references_unchanged = true
```

每个正式候选均执行 ManufacturingIR 对照和保存后重新打开验证。拆板后参考只在 source-only 编译冻结以后读取，不参与生产求解。

## 项目 2 独立语料

只读输入：`D:\DevData\项目2_BOX_dxf`

```text
input_count = 30
passed = 30
failed = 0
proof_disposition.auto_accept = 30
saved_dxf_reopens_and_matches = 30
inputs_unchanged = true
```

所有候选只写入系统临时目录，测试完成后自动移除。

## 单内核与来源证明

`scripts/verify_box_v1_source.py` 实测：

```text
tag = v1.0.0
commit = 5a2be1a82eb7235bcff62d97a13d2937f9ad026b
matched = 23
patched = 7
missing = 0
changed = 0
unexpected = 0
ok = true
```

初次压缩融合时 30 个项目 2 核心文件逐字节匹配上游；后续
`box-notch-hotfix-2026-07-21` 在 7 个文件上形成声明补丁，当前校验结果为 23 个原样
文件加 7 个双向哈希锁定补丁。架构测试仍证明旧顶层 BOX 模块、外部 distribution、
backend 开关、`SplitAssembly` 降级链和 legacy 节点没有进入生产调用图。

## 性能诊断

- 一个代表性 Assembly 真实求解测试约 23.25 秒；该文件收集 44 个测试，因此先前 184/304 秒组合命令超时是总测试量导致，不是死锁。
- 对包含 3,900 个 SourceIR 实体的真实样例重复执行预算检查 1,000 次耗时约 2.38 秒，即单次约 2.38 毫秒；未发现本次预算门造成显著求解性能回归。

## 剩余风险和 BH 后续边界

1. 资源预算当前位于 SourceIR 构建完成后、analysis/solve 之前。它能阻止超限输入进入算法搜索，但不是 DXF 解码器本身的预解析沙箱；若以后需要抵御恶意超深块或超大文件，应在保持来源证明的前提下增加流式/预解析预算，而不是恢复旧 solver。
2. station track、end chain 等复杂旧重建能力没有迁移，因为两套权威语料没有证明项目 2 缺失。若出现新的失败 DXF，必须重新按“先失败测试、再项目 2 原生最小补丁”处理。
3. `steel-dxf-split v1.5.1` 尚未覆盖当前 BH；BH/BOX 最终统一主干和框架整理仍是下一阶段。
4. 未执行 commit、push、主干合并或发布。
