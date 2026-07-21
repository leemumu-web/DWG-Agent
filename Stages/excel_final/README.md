# Excel Final 处理阶段

这是独立 Python 3.11+ 钢结构零件清单处理器。它接受受支持的 Tekla 文本导出或包含必要初始表 schema 的真实工作簿，并生成规范化多 sheet `.xlsx`；[PROCESS.md](PROCESS.md) 是逐步算法手册。

平台不把此目录作为 backend package 导入。`backend/app/modules/excel_processing/stage_adapter.py` 是父进程唯一入口，`stage_runner.py` 在隔离子进程执行本目录 `main.py`，并设置有界 timeout 和结构化 JSON adapter。Celery、MySQL、存储、权限和 attempt 仍由 backend 管理。

支持输入边界：

- 文本 `.xls`：Tekla tab/whitespace 导出，不一定是二进制 workbook；
- 二进制 `.xls`：文本探测失败后由锁定 `xlrd` 解析；
- `.xlsx`/`.xlsm`：必须包含必要初始表 signature，扩展名本身不够；
- `hardware_handbook`：型材重量的只读 MySQL 参考数据。

```bash
uv sync --locked
uv run python main.py /path/to/input.xls -o /path/to/output.xlsx
uv run pytest -q multi_split/tests
```

Stage 测试重点覆盖型材拆分和 VBA parity；平台 adapter/import/retry/error 测试位于 `backend/tests`。两者都不能证明支持每份企业工作簿，验收需要代表性正反样本和输出复核。

## 顶层源码分工

| 文件 | 实际责任 |
|---|---|
| `reader.py` | 读取 Tekla 文本/旧 XLS，识别编码、分隔形式和表头，形成清洗后的原表。 |
| `reader_init.py` | 读取九列“初始表”，建立构件与零件行的结构化输入。 |
| `parser.py` | 判定构件起止行、合计行等行类型。 |
| `spec_parser.py` | 分类规格字符串并解析板件尺寸，不负责数据库查重。 |
| `transformer.py` | 执行传统输入的第 2–9 步列设置、sheet 拆分与整理表变换。 |
| `transform_init.py` | 把“初始表”转换为拆板后整理表所需的 DataFrame，并完成拆分、排序、计算与编号。 |
| `multi_split_bridge.py` | 第 10 步调用 vendored `multi_split` 框架，是两套输入流共用的拆分接缝。 |
| `post_split.py` | 执行第 11–14 步拆分后修正。 |
| `calculator.py` | 执行第 15–19 步计算列与重量计算。 |
| `prorate.py` | 对 BH/I/BT 等拆分行进行重量分摊。 |
| `finalize.py` | 执行第 20–24 步输出整理与正确性检查。 |
| `writer_parts.py` | 写入初始表链路的规范 sheet 和零件 sheet。 |
| `handbook.py` | 以只读 MySQL 查询型钢理论重量；连接生命周期与查询失败保持显式。 |
| `pipeline.py` | 编排传统 25 步链路和初始表链路，保持步骤顺序的唯一入口。 |
| `config.py` | 保存列关键词、路径和手册数据库配置；秘密值只从环境读取。 |
| `utils.py` | 提供单元格安全转换、列查找/增删、去空格和序号等纯工具。 |
| `pyproject.toml` | 定义独立 Stage 的 Python 版本、运行依赖和测试配置。 |

`main.py` 仍是 CLI，`pipeline.py` 才是算法编排入口；平台不得绕过 adapter 直接把这些模块
当作长驻服务导入。文件分工不表示每个步骤都已通过企业全部工作簿验收。

源输入字节保留在平台存储；输出工作簿的 `原表` 是去除半角/全角空格后的处理基线，不是逐字节备份。禁止把手册密码、child traceback、DSN 或 host path 写入公共 Job 错误。
