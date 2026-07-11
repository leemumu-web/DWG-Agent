# Excel Final Stage / Excel Final 处理阶段

## English

Standalone Python 3.11+ steel part-list processor. It accepts supported Tekla text exports or real workbooks with the required initial-table schema and writes a normalized multi-sheet `.xlsx`. `PROCESS.md` is the detailed Chinese algorithm handbook.

The platform does not import this tree as a backend package. `app.integrations.excel_final_runner` starts `main.py` in an isolated child process with a bounded timeout and structured JSON adapter. Celery/MySQL/storage/permission/attempt behavior remains owned by the backend.

### Inputs and Dependencies

- Text `.xls`: Tekla tab/whitespace export, not necessarily a binary workbook.
- Binary `.xls`: parsed by locked `xlrd` when text detection fails.
- `.xlsx`/`.xlsm`: must contain the required initial-table signature; extension alone is insufficient.
- `hardware_handbook`: read-only MySQL reference data for profile weights.

```bash
uv sync --locked
uv run python main.py /path/to/input.xls -o /path/to/output.xlsx
uv run pytest -q multi_split/tests
```

The test suite focuses on profile splitting and VBA parity. Platform adapter/import/retry/error tests live under `backend/tests`. Neither proves every real enterprise workbook; acceptance requires representative valid/invalid samples and output review.

Do not log handbook passwords, child tracebacks, DSNs, or host paths into public Job errors. The source input bytes remain in platform storage; the output workbook's `原表` sheet is a whitespace-cleaned baseline, not a byte-for-byte backup.

## 中文

这是独立 Python 3.11+ 钢结构零件清单处理器。它接受受支持的 Tekla 文本导出或含必要初始表 schema 的真实工作簿，并生成规范化多 sheet `.xlsx`。`PROCESS.md` 是详细中文算法手册。

平台不把此树作为 backend package 导入。`app.integrations.excel_final_runner` 以有界 timeout 和结构化 JSON adapter 在隔离子进程启动 `main.py`。Celery/MySQL/storage/permission/attempt 行为仍由 backend 负责。

### 输入与依赖

- 文本 `.xls`：Tekla tab/whitespace 导出，不一定是二进制 workbook。
- 二进制 `.xls`：文本探测失败时由锁定 `xlrd` 解析。
- `.xlsx`/`.xlsm`：必须含必要初始表 signature；扩展名本身不够。
- `hardware_handbook`：型材重量的只读 MySQL 参考数据。

```bash
uv sync --locked
uv run python main.py /path/to/input.xls -o /path/to/output.xlsx
uv run pytest -q multi_split/tests
```

测试套件重点覆盖 profile splitting 和 VBA parity。平台 adapter/import/retry/error 测试位于 `backend/tests`。二者都不能证明支持每份真实企业工作簿；验收需要代表性有效/无效样本和输出复核。

禁止把手册密码、child traceback、DSN 或 host path 记录到公共 Job error。源输入字节保留在平台存储；输出工作簿的 `原表` sheet 是去空格后的基准，不是逐字节备份。
