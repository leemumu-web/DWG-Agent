# 统一双产物验证

## 最小开发回归

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_hole_color_policy_v1.py `
  tests\test_paired_output_validation.py `
  tests\test_unified_paired_pipeline.py `
  tests\test_unified_cli_contract.py `
  -q
```

这组测试覆盖唯一判型/拆板、普通版与余量版同源派生、左右孔颜色、颜色篡改拒绝、两份 DXF 数量、跨路由清理和目录提升回滚。

## BH 真实样例

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\bh_v152\test_bh_hole_color_policy.py `
  tests\bh_v152\test_bh_automatic_weld_allowance.py `
  -q
```

`2b1-cb-29` 必须证明：

- 普通版有 24 个 ACI 1 左孔与 24 个 ACI 7 右孔；
- 余量版保持相同孔洞几何和颜色；
- 至少一个板件获得正余量；
- 一个输入任务目录恰好有两个 DXF。

完整 BH 冻结语料回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\bh_v152\test_bh_corpus_regression.py -q
```

## BOX 验证

BOX 合成孔洞回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\box_v1\test_writer.py -q
```

它构造带横向镜像圆孔的 BOX 制造 IR，验证写入器与保存后验证器都执行左红右白，并拒绝把右孔篡改为红色。

BOX 权威 20 对只读语料：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\box_v1\test_golden_corpus.py -q
```

当前 20 对权威语料没有横向镜像圆孔对，因此该门验证拆板核心但不能替代真实 BOX 颜色样例。新增真实样例时必须进入只读成对验收。

## 混合输入与产出数量

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_unified_cli_contract.py::test_real_mixed_bh_box_directory_is_processed_once_into_four_paired_dxfs `
  -q
```

该测试把一个真实 BH 与一个真实 BOX 放入同一输入目录，必须得到：判型 2 次、BH/BOX 拆板各 1 次、BH/BOX 余量各 1 次、最终 4 个 DXF。

## 来源与安装包

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_bh_v152_package.py `
  tests\box_v1\test_source_import_verifier.py `
  tests\box_v1\test_release.py `
  -q
```

使用干净源快照构建并检查 wheel：

```powershell
.\.venv\Scripts\python.exe scripts\build_unified_wheel.py `
  --output-dir .\release\unified-paired
.\.venv\Scripts\python.exe -m pytest tests\test_unified_wheel_build.py -q
```

- 构建器只复制 `pyproject.toml`、`README.md`、`uv.lock` 和 `src/`，不读取仓库中的 `build/`、`dist/` 或旧 `release/`；
- console script 只有 `steel-dxf-split`；
- 不包含根级或 BOX 级旧批处理/余量 CLI；
- 不包含旧独立余量发布复扫器；
- BOX 内置 attestation 与当前统一入口、成对验收、孔洞策略、核心代码及构建合同指纹一致。

## 全量验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

只有本轮实际运行并得到的结果才能写入发布报告；不得沿用旧 README 中的测试数字或旧 attestation 声称当前通过。
