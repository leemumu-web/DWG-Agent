# Steel DXF Split PL

这是 PL 折弯板拆板的独立命令启动包。它只把 `steel-dxf-split-pl` 命令转发到隔离的 `steel_dxf_split.pl` 领域模块，不修改也不调用 BH/BOX 统一入口、双产物流程或合图工具。

在仓库根目录安装：

```powershell
python -m pip install -e .\Stages\steel_dxf_split_v1.5.2
python -m pip install -e .\Stages\steel_dxf_split_pl
```

运行：

```powershell
steel-dxf-split-pl ".\combined.dxf" --output-dir ".\pl-output"
```

每个成功零件输出一张 `<零件号>.dxf`，批次审计报告为 `pl_split_report.json`。默认不覆盖已有结果；需要覆盖时显式增加 `--overwrite`。
