# Steel DXF Split PL

这是 PL 折弯板拆板的独立 Stage。`steel_dxf_split_pl` 自己拥有 PL 解析、几何证明、展开、输出、批次报告和 CLI，不导入或调用 BH/BOX 统一拆板包、双产物流程或合图工具。

在仓库根目录安装：

```powershell
python -m pip install -e .\Stages\steel_dxf_split_pl
```

运行：

```powershell
steel-dxf-split-pl ".\combined.dxf" --output-dir ".\pl-output"
```

每个成功零件输出一张 `<零件号>.dxf`，批次审计报告为 `pl_split_report.json`。默认不覆盖已有结果；需要覆盖时显式增加 `--overwrite`。

Python interface：

```python
from steel_dxf_split_pl import split_pl

batch = split_pl("input.dxf", "pl-output")
```
