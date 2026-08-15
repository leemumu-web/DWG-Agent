# BH_optimization 工作区

BH 拆板算法研究与优化工作区。研究对象：`Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/` 的 `bh_*` 模块。

## 环境

### 立即可用（推荐）

直接使用拆板 Stage 自带的 venv（Python 3.13.13，依赖已装齐）：

```bash
VENV="$PWD/../Stages/steel_dxf_split_v1.5.2/.venv/bin/python"
export MPLCONFIGDIR="$PWD/.mplconfig"   # matplotlib 缓存写到工作区，避免 ~/.config 只读
"$VENV" -c "import steel_dxf_split; print(steel_dxf_split.__version__)"   # 1.5.2
```

### 本工作区自建 venv（Python 3.12，供未来扩展 bh_left_right_reader 用）

- `.python-version` 固定 `3.12`（`bh_left_right_reader` 要求 `<3.13`，`steel_dxf_split` 要求 `>=3.12,<3.14`，交集是 3.12）。
- `pyproject.toml` 以 editable path 依赖两个 BH 相关包。
- 依赖同步命令（首次下载较慢，需网络）：

```bash
export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR="$PWD/.uv_cache"   # 避免写到只读的 ~/.cache
uv sync
```

> 注：`~/.local/bin/uv` 不在默认 PATH；系统 Python 是 3.14（过新），必须用 uv 拉 3.12。

## 实证脚本（已跑通）

- `sample_input/`：从 `BH_拆板前_dxf汇总` 取的 10 个样本 DXF。
- `manifest.json`：10 样本的 BH 分类清单。
- `sample_output/`：10/10 auto_accepted 的拆板结果（正常拆板 + 余量增长 + 报告）。
- `single_output/`：单样本 `2b1-cb-40` 的完整报告（含 automation_assessment / 32 条 proof obligations / capabilities）。

复现完整报告：

```bash
VENV="$PWD/../Stages/steel_dxf_split_v1.5.2/.venv/bin/python"
export MPLCONFIGDIR="$PWD/.mplconfig"
"$VENV" -m steel_dxf_split.cli single_input -o single_output \
  --classification-manifest single_manifest.json \
  --authorize-tekla-bh-single-part-profile project_tekla_bh_dxf_v1
```

## 结论

详见 [`BH拆板算法根本不足分析.md`](./BH拆板算法根本不足分析.md)。
