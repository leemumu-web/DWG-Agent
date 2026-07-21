# Workflow 输入接收

## 现有实现

`registration.py` 登记/补交多个 DWG 与单 Excel 并生成规范名；`conversion.py` 创建/同步 DWG→DXF Job；`freeze.py` 校验格式、重复、缺失、同名冲突后冻结 manifest/drawing unit；`presentation.py` 投影配对、错误和操作员状态。

## 输入、输出与边界

输入是 workflow、已登记 file ID、files/jobs/CAD 公共接口，输出是服务器派生 DXF、逐图配对诊断与不可变输入清单。人工 DXF 始终拒绝，冻结后源文件删除受保护。
