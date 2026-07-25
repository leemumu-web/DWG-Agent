# 统一输入输出合同

## 输入

公开命令接收一个输入目录，不接收输出文件列表：

```powershell
uv run steel-dxf-split `
  ".\input" `
  --output-dir ".\output" `
  --authorize-tekla-bh-single-part-profile project_tekla_bh_dxf_v1 `
  --authorize-tekla-box-single-part-profile project_tekla_box_dxf_v1
```

输入合同：

- 只枚举目录第一层的普通 `.dxf` 文件；
- 忽略子目录、符号链接和非 DXF 文件；
- 启动时一次性冻结并排序输入列表；
- 输入与输出不得相同或互相嵌套；
- 去掉 `_拆板前` 后的任务名不得冲突；
- 每个实际出现的 BH/BOX 类型都必须有对应来源授权。

## 自动验收输出

一张成功输入固定写入：

```text
output/auto_accepted/<bh|box>/<任务名>/
├─ <任务名>_normal.dxf
├─ <任务名>_weld_allowance.dxf
├─ <任务名>_report.json
├─ <任务名>_weld_allowance_report.json
└─ previews/
```

任务目录中必须恰好有两个 DXF。两份都是包含全部拆板板件的完整图，不按板件拆成多个文件。

`normal` 只做正常拆板与孔洞颜色处理。`weld_allowance` 从该普通结果复制派生，只做规定的余量伸长；它不得重新判型、重新拆板或重新识别孔洞。

## 人工复核输出

任一领域证明或双文件验收未通过时，整个任务写入：

```text
output/manual_review/<bh|box>/<任务名>/
```

目录包含统一报告以及可安全保留的源图或普通候选。它不是生产输出，不保证存在一对可制造 DXF。相同任务一旦进入 `manual_review`，旧 `auto_accepted` 任务目录必须被事务性移除。

## JSON 摘要

标准输出是一个 JSON 数组。每个元素至少包含：

- `input`、`family`；
- `automation_route` 与 `native_automation_route`；
- `production_clean`、`weld_allowance`、`report`、`weld_allowance_report`；
- `task_dir`、`previews`；
- 证明处置、诊断码、制造指纹与处理耗时。

路径不存在时必须为 `null`，不得指向临时目录。

## 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 所有输入均成对进入 `auto_accepted` |
| `1` | 至少一个任务形成了可审计的 `manual_review` |
| `2` | 参数错误，或至少一个输入发生无法形成审计任务的异常 |

## 禁止事项

- 不导入或调用旧 `weld_allowance_cli`；
- 不递归扫描 `output`；
- 不让余量程序扫描普通版目录；
- 不为一个输入重复判型或重复拆板；
- 不把 BH 文件交给 BOX 核心，反之亦然；
- 不在 `auto_accepted` 留下单份 DXF；
- 不在失败后调用已退役入口。
