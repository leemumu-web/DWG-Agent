# Excel Final 处理阶段

这是独立 Python 3.11+ 钢结构零件清单处理器。它接受受支持的 Tekla 文本导出或包含必要初始表 schema 的真实工作簿，并生成规范化多 sheet `.xlsx`；[PROCESS.md](PROCESS.md) 是逐步算法手册。

平台不把此目录作为 backend package 导入。`app.integrations.excel_final_runner` 在隔离子进程执行 `main.py`，并设置有界 timeout 和结构化 JSON adapter。Celery、MySQL、存储、权限和 attempt 仍由 backend 管理。

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

源输入字节保留在平台存储；输出工作簿的 `原表` 是去除半角/全角空格后的处理基线，不是逐字节备份。禁止把手册密码、child traceback、DSN 或 host path 写入公共 Job 错误。
