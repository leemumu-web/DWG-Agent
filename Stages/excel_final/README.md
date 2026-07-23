# Excel Final

Excel Final 把 Tekla 构件零件清单或 DWG“初始表”规范为可审计的钢结构零件数据库工作簿。两个输入入口只负责适配，之后共同进入同一条分类、五金手册、重量核验、拆板、`part` 和写表引擎。

## 运行

```bash
uv run python main.py /path/to/input.xlsx -o /path/to/output.xlsx
uv run pytest -q -m "not handbook_mysql and not live_data" tests multi_split/tests
```

`.xlsx` / `.xlsm` 生产工作簿必须恰好一张 sheet；多 sheet 的复核文件必须先用 `tools/preprocess_ground_truth.py` 分离原表。Tekla 文本允许使用 `.xls` 后缀，但内容必须是可识别的文本表格。规范结果始终新建为 `.xlsx`，显式指定其他输出后缀会被拒绝，不复制源宏。

五金手册配置不写在 Stage 中。平台通过隔离子进程注入只读 MySQL 配置；连接、schema 或查询故障均为致命错误。

## 固定六表

输出顺序恒定为：`原表`、`清洗表`、`构件表`、`整理表`、`part`、`处理报告`。

- `原表`：保留生产输入的值、样式和原始空格。
- `清洗表`：不可变父零件记录及规范分类。
- `构件表`：每个构件一个 `summary`，合并起始身份与小计重量/尺寸；来源 sheet、行类型和小计来源行默认隐藏。
- `整理表`：父件或 BH/BOX/BT 子板，含身份、手册来源、重量链和核验状态；比重来源、净材利用率和重量核验默认隐藏。
- `part`：固定 11 列下料投影，无标题行或合计行偏移。
- `处理报告`：只列警告、严重和致命的人工处置项；同源同类问题合并并提供建议操作，无问题时显示“无”。

长度存在时，`下料长度`保留 Excel 公式，同时写入可被 `data_only=True` 立即读取的公式缓存；长度缺失时公式与缓存均留空。处理结果返回 `PipelineOutcome`，其中包含输出路径、质量状态、警告计数、严重计数和报告摘要；平台通过版本化协议读取这些字段，不再对输出工作簿做二次修补。

缺构件编号、零件号、规格、长度、材质或数量，以及非正长度/数量/构件数、负重量/面积或 NaN/Infinity 等非有限数值的来源行，不查询手册或拆板；它们保留在清洗表、整理表和处理报告并逐字段标红，但不进入 `part`。构件 summary 的数量、尺寸、重量或面积非法时隔离整个构件。

## 责任与边界

本目录负责单个 Tekla/初始表输入的规范化、手册查询、物理核验、拆板、`part` 投影和六表输出。它不负责 Files/Job 权限、Celery 编排、对象存储或跨图纸最终汇总；这些能力由 backend 平台拥有。

## 主要模块

| 模块 | 责任 |
|---|---|
| `config.py` | 平台注入的只读手册数据库配置 |
| `domain.py` | 不可变规范记录与 `PipelineOutcome` |
| `input_contract.py` | 单 sheet 输入合同和唯一题头检测 |
| `preprocess.py` | 从复核多表工作簿中分离原始输入表 |
| `reader.py` / `reader_init.py` | Tekla 与初始表适配为 `SourcePart` |
| `spec_parser.py` | 材质感知、确定性的规格分类 |
| `handbook.py` | 类别门控、只读 MySQL 查询 |
| `quality.py` | 结构化问题台账与质量摘要 |
| `weights.py` | 未舍入理论重与源重量物理核验 |
| `splitter.py` | 仅 BH/BOX/BT 的规范拆板 |
| `part_builder.py` | `part` 准入、身份冲突检测与逐构件汇总 |
| `canonical_pipeline.py` | 共享生产引擎 |
| `writer_parts.py` / `ooxml_formula.py` | 固定六表、样式、报告和公式缓存 |
| `pipeline.py` | 两个薄输入入口与数据库生命周期 |
| `utils.py` | 安全的字符串与数值规范化小工具 |
| `main.py` | 命令行入口与最终质量提示 |
| `pyproject.toml` | Stage 依赖、测试 marker 与工具配置 |

完整规则见 [PROCESS.md](PROCESS.md)。
