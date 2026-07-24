# Workflow 输入接收

## 现有实现

`registration.py` 分别校验并登记 `.xls`/`.xlsx` 单文件与 DWG 文件夹，并生成规范名。Excel 通过 Excel Final 阶段一的版本化规则检查；成功时保存检查摘要、规则版本和对象 SHA-256，失败请求返回 422 并回滚，不留下已提交的 File 或 WorkflowInputItem。`conversion.py` 只在有效 Excel 与 DWG 同时存在时创建/同步 DWG→DXF Job；`freeze.py` 按登记时 SHA-256 复检 Excel，并在创建任何 Drawing 前校验格式、重复、缺失和同名冲突；`presentation.py` 投影配对、表格错误和操作员下一步动作。

## 输入、输出与边界

输入是 workflow、已登记 file ID、files/jobs/CAD/Excel 公共接口，输出是服务器派生 DXF、逐图配对诊断、Excel 检查账本与不可变输入清单。人工 DXF 始终拒绝，冻结后源文件删除受保护；旧批次若没有 Excel 检查快照，必须移除并重新登记，不能静默冻结。
