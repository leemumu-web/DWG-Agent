# Excel Final 有界自适应列宽实施计划

> **For Codex:** 按测试驱动顺序逐项实施；每项完成后运行对应验证并提交，最终执行真实样表、MySQL、后端链路与全量回归。

**Goal:** 让 Excel Final 的五张生成表按实际内容自适应列宽，同时保持原表、业务数据和可见字段公式，并在最终 Excel 删除整理表的4个内部字段及part类型列。

**Architecture:** 在`writer_parts.py`的最终格式化阶段先保存可选的完整内部导入副本，再按表头删除整理表的类型、比重来源、净材利用率、重量核验列和part类型列，并集中计算文本显示宽度。普通列使用8–32的边界，处理报告长文本列使用16–48并换行；后端从任务临时副本入库，只持久化保留可见公式的最终文件。

**Tech Stack:** Python 3.13、openpyxl、pytest、Unicode East Asian Width

---

## Task 1：以测试固定列宽合同

**Files:**

- Modify: `Stages/excel_final/tests/test_writer_workbook.py`

- [x] **Step 1：增加显示宽度单元测试**

覆盖 ASCII、中文/全角字符、多行文本和空值，明确中文按双宽、换行取最长行。

- [x] **Step 2：增加工作簿格式合同测试**

设置原表自定义列宽并断言输出保持不变；断言生成表普通列位于 8–32，内容较长时宽度随之增长，报告“说明”“建议操作”位于 16–48 且数据单元格启用换行和顶部对齐。

- [x] **Step 3：增加最终删列断言**

断言构件表与整理表的 6 个内部审计列不出现在最终表头，同时内部问题仍进入处理报告。

- [x] **Step 4：运行测试并确认失败原因**

Run: `uv run pytest Stages/excel_final/tests/test_writer_workbook.py -q`

Expected: 新增测试因显示宽度帮助函数和自适应宽度尚未实现而失败。

## Task 2：实现有界自适应列宽

**Files:**

- Modify: `Stages/excel_final/writer_parts.py`
- Test: `Stages/excel_final/tests/test_writer_workbook.py`

- [x] **Step 1：实现 Unicode 显示宽度**

使用 `unicodedata.east_asian_width`，Wide/Fullwidth 字符计 2，其他字符计 1；多行取最长行，空值计 0。

- [x] **Step 2：用表名和表头配置宽度边界**

删除旧 `_CANONICAL_WIDTHS` 固定列号表。五张生成表默认使用 8–32，处理报告两个长文本列使用 16–48。

- [x] **Step 3：完成最终格式化**

内容、公式和质量样式写入后删除最终不展示的内部列，再统一计算列宽；报告长文本数据单元格设置自动换行和顶部对齐。不得处理原表。

- [x] **Step 4：运行 writer 与 Stage 聚焦测试**

Run: `uv run pytest Stages/excel_final/tests/test_writer_workbook.py Stages/excel_final/tests/test_pipeline_end_to_end.py -q`

Expected: PASS

- [x] **Step 5：提交实现**

Commit message: `feat(excel-final): size generated columns to content`

## Task 3：更新真实样表与后端契约

**Files:**

- Modify: `Stages/excel_final/tests/test_ground_truth_regression.py`
- Modify: `backend/tests/excel_processing/test_excel_final_live_flow.py`
- Modify: `Stages/excel_final/README.md`
- Modify: `Stages/excel_final/PROCESS.md`

- [x] **Step 1：在真实 GT 回归中检查生成表宽度**

断言五张生成表宽度均满足边界、报告长文本列换行、6 个内部列不存在，并继续保留 122 行 part 与 GT 对账合同。

- [x] **Step 2：在后端真实链路中检查下载文件**

上传、worker、导入、目录与下载后，检查下载工作簿的 part 行数和有界列宽，证明格式经过后端输入输出流保留。

- [x] **Step 3：更新流程说明**

简洁记录五张生成表自适应列宽、报告长文本换行、原表列宽保真的行为。

- [x] **Step 4：运行真实样表、MySQL 和后端聚焦验证**

Run: `uv run pytest Stages/excel_final/tests/test_ground_truth_regression.py -q`

Run: `backend/.venv/bin/pytest Stages/excel_final/tests/test_handbook_mysql.py -q`

Run: `backend/.venv/bin/pytest backend/tests/excel_processing/test_excel_final_live_flow.py -q`

Expected: PASS

- [x] **Step 5：提交集成与文档**

Commit message: `test(excel-final): verify adaptive widths end to end`

## Task 4：完整回归、产物审查与收尾

**Files:**

- Modify: `Stages/excel_final/data/results/*.xlsx`
- Modify: `Stages/excel_final/data/reports/*.md`
- Modify: `docs/superpowers/plans/2026-07-23-excel-bounded-auto-width.md`

- [x] **Step 1：重新生成真实规范结果**

使用现有 Excel Final CLI/回归入口重新生成首都体育学院样表结果与精炼报告。

- [x] **Step 2：逐表审查最终工作簿**

检查六张 sheet、122 行 part、五张生成表列宽边界、原表列宽、最终删列、报告“无”或核心问题、绝对路径文件列、重量/GT 对账和公式缓存。

- [x] **Step 3：执行完整验证**

Run: `uv run pytest Stages/excel_final/tests -q`

Run: `backend/.venv/bin/pytest Stages/excel_final/tests/test_handbook_mysql.py -q`

Run: `backend/.venv/bin/pytest backend/tests -q`

Run: `uv run ruff check Stages/excel_final`

Run: `backend/.venv/bin/alembic check`

Run: `backend/.venv/bin/python scripts/docs/check.py`

Expected: 全部 PASS，无新增迁移操作。

- [x] **Step 4：清理与自审**

删除确认无意义的旧固定列宽配置和本次产生的缓存文件；检查 `git diff`、`git status` 和提交历史，确保仅样表产物目录按既有约定保持未跟踪。

- [x] **Step 5：完成计划并提交**

将本计划步骤全部勾选，提交最终文档状态。

## Task 5：最终删列但保留后端审计功能

**Files:**

- Modify: `Stages/excel_final/writer_parts.py`
- Modify: `Stages/excel_final/pipeline.py`
- Modify: `backend/app/modules/excel_processing/stage_adapter.py`
- Modify: `backend/app/modules/excel_processing/stage_runner.py`
- Modify: `backend/app/modules/excel_processing/execution.py`
- Test: `Stages/excel_final/tests/test_writer_workbook.py`
- Test: `backend/tests/excel_processing/test_excel_final_adapter.py`
- Test: `backend/tests/excel_processing/test_excel_final_retry.py`
- Test: `backend/tests/excel_processing/test_excel_final_live_flow.py`

- [x] **Step 1：按表头物理删除最终 Excel 的 6 个内部列**

删除后重设构件表和整理表的自动筛选范围，再基于最终列计算有界自适应宽度。

- [x] **Step 2：同一次 writer 生成完整内部导入副本**

内部副本保留手册来源、利用率、核验状态和构件来源，并与最终副本分别完成公式缓存回读。

- [x] **Step 3：后端入库与下载分流**

隔离 Stage 在任务临时目录生成内部副本；数据库从内部副本导入，对象存储和下载只使用最终删列文件，失败时清理内部副本。

- [x] **Step 4：真实链路验证功能保留**

真实上传/worker/入库/下载测试断言数据库审计字段非空、D24/D30 来源为圆钢表，同时下载工作簿不含 6 个内部列。
