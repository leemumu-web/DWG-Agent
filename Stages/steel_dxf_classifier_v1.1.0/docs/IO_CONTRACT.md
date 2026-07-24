# 1.2 输入输出契约

本文件是 Steel DXF Classifier 1.2.0 的正式进程与文件系统接口定义。除非后续发布明确升级 schema，自动化调用应以本文件为准。

项目目录可以移动或重命名；移动后运行 `uv sync --reinstall --frozen` 重写 `.venv` 的入口脚本，再执行分类命令。

## 1. 调用与输入

```text
steel-dxf-classify [--json] [--overwrite] <项目名称>_dxf
steel-dxf-classify --version
```

输入必须是存在的目录，名称精确匹配 `<项目名称>_dxf`，且项目名不能为空。程序只读取该目录第一层扩展名为 `.dxf`（大小写不敏感）的普通文件；不会递归读取子目录。

在标题栏解析前，输入第一层 DXF 原地重命名为 `*_拆板前.dxf`。该阶段先扫描并检查全部命名冲突，再以临时名两阶段改名；任何冲突或失败都会停止，避免部分改名。重复运行不叠加后缀。预处理只改文件名，DXF 字节内容保持不变。

已有同项目输出且没有 `--overwrite` 时，程序先失败，随后不执行预处理。因此失败调用不会因为已有输出而改动输入名。

## 2. 文件系统输出

成功处理后，在输入目录同级产生实际需要的目录：

```text
<项目名称>_<零件类型>_dxf/
<项目名称>_待确认_dxf/
<项目名称>_无法读取_dxf/
<项目名称>_分类报告.json
<项目名称>_分类清单.csv
```

每个输出 DXF 是预处理后输入的逐字节副本。`<项目名称>_分类报告.json` 的 schema 固定为 `STEEL-DXF-CLASSIFICATION-1.2`，包含汇总、每个源文件、标题栏候选、诊断和输出目录；CSV 是同一结果的人工筛选视图。

每个逐图结果还包含以下稳定语义字段：

| 字段 | 含义 |
|---|---|
| `profile_raw` | 标题栏恢复后的原始规格 |
| `profile_normalized` | 用于判定的规范化规格 |
| `type_source` | `catalog`、`auto_discovered` 或非自动分类处置来源 |
| `group_key` | 类型文件夹键 `type:<类型>`，或 `status:review_required` / `status:unreadable` |
| `next_stage_eligible` | 是否允许下一阶段直接读取该 DXF |

自动发现类型必须满足安全前缀和唯一标题栏证据规则。自动发现不等于预警；只有待确认和无法读取结果要求人工处置，且其 `next_stage_eligible` 固定为 `false`。

默认不会覆盖已有输出。`--overwrite` 在隐藏 staging 目录完整生成新副本与报告、核对数量后，备份并替换旧输出；提升失败会恢复旧输出。输入目录从不属于输出替换集合。

## 3. stdout

默认模式输出中文人类摘要，例如：

```text
项目: 项目2
输入: 171
已分类: 171
待确认: 0
无法读取: 0
  BH: 141
  BOX: 30
耗时: 39.654 秒
```

`--json` 模式的 stdout 只输出一个 UTF-8 JSON 对象和一个换行，不混入进度、日志或错误。对象 schema 为 `STEEL-DXF-CLI-1.2`：

```json
{
  "exit_code": 0,
  "schema": "STEEL-DXF-CLI-1.2",
  "status": "completed",
  "summary": {
    "classified_count": 171,
    "input_count": 171,
    "project_name": "项目2",
    "review_required_count": 0,
    "type_counts": {"BH": 141, "BOX": 30},
    "unreadable_count": 0
  }
}
```

当流程完成但存在待确认或无法读取文件时，`status` 为 `completed_with_review`、`exit_code` 为 2，仍输出完整 JSON。CLI JSON 是摘要，不替代分类报告中的逐图审计信息。

## 4. stderr 与退出码

正常完成时 stderr 为空。失败时 stdout 为空，stderr 仅输出一行 `错误: <原因>`；不输出 Python traceback。

| 退出码 | 状态 | stdout | stderr |
| ---: | --- | --- | --- |
| 0 | 全部自动分类 | 人工摘要或单 JSON 对象 | 空 |
| 2 | completed_with_review | 人工摘要或单 JSON 对象 | 空 |
| 1 | 运行失败：命名冲突、已有输出、I/O 或事务失败 | 空 | `错误:` 原因 |
| 64 | 调用/输入契约错误：未知参数、缺少输入、目录名不合法 | 空 | `错误:` 原因 |

`steel-dxf-classify --version` 向 stdout 输出单行 `steel-dxf-classifier 1.2.0`，退出码为 0，stderr 为空。
