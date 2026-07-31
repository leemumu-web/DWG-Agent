# Tekla Header Unit Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Excel 第一阶段、初始表适配器和第二阶段数据库导入统一兼容表头后的单位标注，同时保持业务字段语义不变。

**Architecture:** 在各自运行边界提供同规则的纯表头规范化函数：清理全半角空白、括号/方括号单位和明确的分隔单位后缀，再交给现有别名映射。输出工作簿的固定表头合同仍保持不变，Stage2 的业务基线不被单位兼容逻辑放宽。

**Tech Stack:** Python 3.12, openpyxl, pytest, regex.

---

## Task 1: 锁定带单位表头的失败行为

**Files:**
- Modify: `Stages/excel_final/tests/test_input_contract.py`
- Modify: `Stages/excel_final/tests/test_reader_init.py`
- Modify: `backend/tests/excel_processing/test_excel_final_import.py`

- [ ] 增加 `长度/mm`、`数量（件）`、`单毛重[kg]`、`总表面积（㎡）` 等表头输入。
- [ ] 先运行对应测试，确认当前实现不能完整识别这些格式。

## Task 2: 实现 Excel Final 输入层规范化

**Files:**
- Create: `Stages/excel_final/header_normalization.py`
- Modify: `Stages/excel_final/input_contract.py`
- Modify: `Stages/excel_final/reader_init.py`

- [ ] 统一去除全半角空白、尾部括号/方括号单位和明确分隔的单位后缀。
- [ ] 不删除规格、构件编号或零件号中的业务字符。
- [ ] 复用现有别名、重复列冲突和长度/宽度/高度语义判断。

## Task 3: 实现后端结果导入层规范化

**Files:**
- Create: `backend/app/modules/excel_processing/header_normalization.py`
- Modify: `backend/app/modules/excel_processing/importers.py`
- Modify: `backend/tests/excel_processing/test_excel_final_import.py`

- [ ] 让 `整理表` 和 `处理报告` 的数据库导入同时接受单位后缀表头。
- [ ] 保持重复列、缺少核心列、报告哨兵等现有错误合同不变。

## Task 4: 跨阶段验证

**Files:**
- Test: `Stages/excel_final/tests/test_input_contract.py`
- Test: `Stages/excel_final/tests/test_reader_init.py`
- Test: `backend/tests/excel_processing/test_excel_final_import.py`
- Test: Stage2 regression suite

- [ ] 运行输入合同、初始表、后端导入和 Stage2 测试。
- [ ] 用一个带单位表头的真实 `.xlsx` 复制样本验证第一阶段可读、第二阶段基线和输出固定表头不变。

## Task 5: 提交

- [ ] 检查 `git diff --check`。
- [ ] 只提交本功能相关源文件和测试。
