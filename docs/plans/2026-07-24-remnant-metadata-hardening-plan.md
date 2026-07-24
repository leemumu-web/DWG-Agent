# 余料附加信息与导出安全加固实施计划

## 任务一：锁定后端字段更新契约

- 在 `backend/tests/remnant_inventory/test_confirmation.py` 增加单行编辑省略字段、`null`/空白清空测试。
- 在 `backend/tests/remnant_inventory/test_import_batches.py` 和 `test_api.py` 增加批量只更新指定字段、显式清空、空操作中文错误测试。
- 使用字段哨兵或 Pydantic `model_fields_set` 将“省略”和“清空”传递到领域函数。
- 审计日志只记录实际提交的字段。

## 任务二：加固 Excel 导出

- 在 `backend/tests/remnant_inventory/test_export.py` 增加公式型文本安全测试。
- 在 `backend/app/modules/remnant_inventory/export.py` 增加统一文本安全转换。
- 保持数字和日期单元格类型不变。

## 任务三：修正批量填写界面

- 在 `frontend/tests/e2e/remnant-inventory/import.spec.ts` 增加字段独立更新和明确清空测试。
- 调整 `RemnantConfirmationPanel.tsx`，为两个批量字段增加更新开关，只提交已选择字段。
- 未选择任何字段时禁用确认操作并显示中文说明。
- 更新前端 API 类型，允许部分字段及显式 `null`。

## 任务四：分模块验收

- Ruff 与余料后端测试。
- 余料解析 Stage 测试。
- 前端生产构建。
- 余料 Playwright 按 spec 分组运行，避免全套命令超时掩盖结果。
- 检查迁移 head、工作区状态和差异格式。
- 提交并推送当前分支。

