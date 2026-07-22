# Excel Final

Excel Final 把 Tekla 构件零件清单或 DWG“初始表”规范为可审计的钢结构零件数据库工作簿。两个输入入口只负责适配，之后共同进入同一条分类、五金手册、重量核验、拆板、`part` 和写表引擎。

## 运行

```bash
uv run python main.py /path/to/input.xlsx -o /path/to/output.xlsx
uv run pytest -q -m "not handbook_mysql and not live_data" tests multi_split/tests
```

生产工作簿必须恰好一张 sheet；多 sheet 的复核文件必须先用 `tools/preprocess_ground_truth.py` 分离原表。Tekla 文本允许使用 `.xls` 后缀，但内容必须是可识别的文本表格。

五金手册配置不写在 Stage 中。平台通过隔离子进程注入只读 MySQL 配置；连接、schema 或查询故障均为致命错误。

## 固定六表

输出顺序恒定为：`原表`、`清洗表`、`构件表`、`整理表`、`part`、`处理报告`。

- `原表`：保留生产输入的值、样式和原始空格。
- `清洗表`：不可变父零件记录及规范分类。
- `构件表`：构件起始/小计来源记录。
- `整理表`：父件或 BH/BOX/BT 子板，含身份、手册来源、重量链和核验状态。
- `part`：固定 11 列下料投影，无标题行或合计行偏移。
- `处理报告`：信息、警告、严重问题的逐来源行台账。

`下料长度`保留 Excel 公式，同时写入可被 `data_only=True` 立即读取的公式缓存。处理结果返回 `PipelineOutcome`，其中包含输出路径、质量状态、警告计数、严重计数和报告摘要；它实现了 `os.PathLike` 以兼容现有平台调用。

## 主要模块

| 模块 | 责任 |
|---|---|
| `input_contract.py` | 单 sheet 输入合同和唯一题头检测 |
| `reader.py` / `reader_init.py` | Tekla 与初始表适配为 `SourcePart` |
| `spec_parser.py` | 材质感知、确定性的规格分类 |
| `handbook.py` | 类别门控、只读 MySQL 查询 |
| `weights.py` | 未舍入理论重与源重量物理核验 |
| `splitter.py` | 仅 BH/BOX/BT 的规范拆板 |
| `part_builder.py` | 严格 RECT 证明与逐构件 `part` 汇总 |
| `canonical_pipeline.py` | 共享生产引擎 |
| `writer_parts.py` / `ooxml_formula.py` | 固定六表、样式、报告和公式缓存 |
| `pipeline.py` | 两个薄输入入口与数据库生命周期 |

完整规则见 [PROCESS.md](PROCESS.md)。
