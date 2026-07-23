# 余料候选扩展、全局查看与 Excel 导出实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展余料图纸元数据候选识别，增加多零件人工确认，并为所有工人提供全局余料查看与全量 Excel 导出。

**Architecture:** 保持现有解析结果和数据库契约，通过有优先级的分类规则扩展候选；全局浏览采用独立分页查询以保护原精确查料接口；Excel 在 FastAPI 后端从数据库全量生成。前端复用现有详情和状态操作，只新增全局页签、筛选及下载入口。

**Tech Stack:** Python 3.12、ezdxf、FastAPI、SQLAlchemy、OpenPyXL、React 19、TypeScript 6、Ant Design 6、React Query、pytest、Playwright。

## Global Constraints

- 扫描所有 DXF 空间区域，不按 CAD 视口或空间距离过滤。
- 独立的 2～3 个中文字符标注必须忽略。
- 零件候选支持多选并默认全部选中。
- 所有工人可查看全库和导出全部余料。
- Excel 一张余料一行，零件编号用顿号合并。
- 不做数据库迁移，不破坏现有精确查料 API。

---

### Task 1: 扩展解析分类规则

**Files:**
- Modify: `Stages/remnant_drawing_reader/tests/test_reader.py`
- Modify: `Stages/remnant_drawing_reader/src/remnant_drawing_reader/classifier.py`

**Interfaces:**
- Consumes: `classify(items: list[Evidence])`
- Produces: 现有四元组 `(material_candidates, project_candidates, part_candidates, warnings)`，schema 不变。

- [ ] **Step 1: 写入失败测试**

增加覆盖括号材质、性能后缀、材质与零件复合文字、JWL/ND/DS/YL/3CB/LYTL 编号、中文标题、短中文忽略和普通文字不报警的测试。

- [ ] **Step 2: 验证测试按预期失败**

Run: `uv run --project Stages/remnant_drawing_reader pytest Stages/remnant_drawing_reader/tests/test_reader.py -q`

Expected: 新增测试因候选缺失和 `UNRECOGNIZED_TEXT` 多余而失败。

- [ ] **Step 3: 实现最小分类逻辑**

在 `classifier.py` 增加材质片段、零件片段、中文检测和短中文检测函数，并按“显式标签 → 材质 → 零件 → 短中文 → 项目标题”的顺序分类。删除普通未分类文字生成 `UNRECOGNIZED_TEXT` 的路径。

- [ ] **Step 4: 验证解析测试通过**

Run: `uv run --project Stages/remnant_drawing_reader pytest Stages/remnant_drawing_reader/tests -q`

Expected: 全部通过。

- [ ] **Step 5: 提交解析器变更**

```powershell
git add Stages/remnant_drawing_reader/src/remnant_drawing_reader/classifier.py Stages/remnant_drawing_reader/tests/test_reader.py
git commit -m "feat(remnants): expand drawing metadata candidates"
```

### Task 2: 增加全局余料查询

**Files:**
- Modify: `backend/tests/remnant_inventory/test_api.py`
- Modify: `backend/app/modules/remnant_inventory/inventory.py`
- Modify: `backend/app/modules/remnant_inventory/routes.py`

**Interfaces:**
- Produces: `list_all_remnants(db, *, material_id, thickness_mm, statuses, project, part, sort, page, page_size) -> RemnantPage`
- Produces: `GET /api/v1/remnants/all` 分页响应。

- [ ] **Step 1: 写入失败 API 测试**

测试普通工人无需材质和厚度即可看到全部状态，并验证材质、厚度、项目、零件筛选以及排序、分页。

- [ ] **Step 2: 验证 API 测试失败**

Run: `uv run --project backend pytest backend/tests/remnant_inventory/test_api.py -q`

Expected: `/api/v1/remnants/all` 返回 404。

- [ ] **Step 3: 实现全局查询与路由**

用 SQLAlchemy 构建可选过滤条件；零件筛选通过 `RemnantPart` 子查询；状态默认四种全部包含；允许 `created_desc`、`created_asc`、`thickness_asc`、`thickness_desc`、`status` 排序。路由必须定义在 `/{remnant_id}` 之前并继续调用 `_require_user`。

- [ ] **Step 4: 验证后端查询测试通过**

Run: `uv run --project backend pytest backend/tests/remnant_inventory/test_api.py backend/tests/remnant_inventory/test_confirmation.py -q`

Expected: 全部通过。

- [ ] **Step 5: 提交全局查询**

```powershell
git add backend/app/modules/remnant_inventory/inventory.py backend/app/modules/remnant_inventory/routes.py backend/tests/remnant_inventory/test_api.py
git commit -m "feat(remnants): add global inventory listing"
```

### Task 3: 增加全量 Excel 导出

**Files:**
- Create: `backend/app/modules/remnant_inventory/export.py`
- Create: `backend/tests/remnant_inventory/test_export.py`
- Modify: `backend/app/modules/remnant_inventory/routes.py`

**Interfaces:**
- Produces: `build_remnant_export(db: Session) -> BytesIO`
- Produces: `GET /api/v1/remnants/export.xlsx`，媒体类型为 XLSX，返回带 UTF-8 文件名的附件。

- [ ] **Step 1: 写入失败导出测试**

普通工人调用导出接口，使用 OpenPyXL 重新打开响应，断言工作表名、14列表头、一张余料一行、中文状态、多个零件顿号合并、日期单元格以及空库表头。

- [ ] **Step 2: 验证导出测试失败**

Run: `uv run --project backend pytest backend/tests/remnant_inventory/test_export.py -q`

Expected: 导出路由不存在。

- [ ] **Step 3: 实现工作簿与路由**

在独立模块中以只写工作簿生成“全部余料”，冻结首行、设置筛选、蓝色表头、自动换行和受控列宽。用人员表和文件表读取名称，时区转换为 `Asia/Shanghai` 后写入无时区 Excel 日期。路由返回 `StreamingResponse` 并写审计日志。

- [ ] **Step 4: 验证导出测试通过**

Run: `uv run --project backend pytest backend/tests/remnant_inventory/test_export.py backend/tests/remnant_inventory/test_api.py -q`

Expected: 全部通过，并可由 OpenPyXL 重新读取。

- [ ] **Step 5: 提交 Excel 导出**

```powershell
git add backend/app/modules/remnant_inventory/export.py backend/app/modules/remnant_inventory/routes.py backend/tests/remnant_inventory/test_export.py
git commit -m "feat(remnants): export complete inventory to excel"
```

### Task 4: 改造人工确认的候选选择

**Files:**
- Modify: `frontend/src/features/remnant-inventory/RemnantConfirmationPanel.tsx`
- Modify: `frontend/tests/e2e/remnant-inventory/import.spec.ts`

**Interfaces:**
- Consumes: 现有 `RemnantImportItem.part_candidates` 和 `parts`。
- Produces: 保存时继续提交 `parts: string[]`。

- [ ] **Step 1: 写入失败浏览器测试**

模拟三个零件候选，打开编辑窗口，断言三个候选默认选中；取消一个并添加一个手工编号后保存，断言 PATCH 请求中的 `parts` 只包含最终选择。

- [ ] **Step 2: 验证测试失败**

Run: `npm --prefix frontend run test:e2e -- tests/e2e/remnant-inventory/import.spec.ts`

Expected: 当前文本域不存在标签多选行为，测试失败。

- [ ] **Step 3: 实现候选控件**

将 `partsText` 替换为 `parts: string[]`，使用 `Select mode="tags"`。初始化值优先使用已保存 `item.parts`，否则使用全部去重候选；保存时直接提交规范化后的数组。项目编号改用带候选 options 的 `AutoComplete`，仍允许任意编辑。

- [ ] **Step 4: 验证前端测试通过**

Run: `npm --prefix frontend run test:e2e -- tests/e2e/remnant-inventory/import.spec.ts`

Expected: 全部通过。

- [ ] **Step 5: 提交确认界面**

```powershell
git add frontend/src/features/remnant-inventory/RemnantConfirmationPanel.tsx frontend/tests/e2e/remnant-inventory/import.spec.ts
git commit -m "feat(frontend): confirm multiple remnant parts"
```

### Task 5: 增加全局余料页和下载入口

**Files:**
- Modify: `frontend/src/features/remnant-inventory/types.ts`
- Modify: `frontend/src/features/remnant-inventory/api.ts`
- Create: `frontend/src/features/remnant-inventory/RemnantGlobalPanel.tsx`
- Modify: `frontend/src/features/remnant-inventory/RemnantInventoryPage.tsx`
- Modify: `frontend/src/features/remnant-inventory/styles.css`
- Create: `frontend/tests/e2e/remnant-inventory/global.spec.ts`

**Interfaces:**
- Produces: `listAllRemnants(search: RemnantGlobalSearch)` 调用 `/api/v1/remnants/all`。
- Produces: `exportAllRemnants()` 下载 `/api/v1/remnants/export.xlsx`。

- [ ] **Step 1: 写入失败浏览器测试**

验证“全部余料”页签自动加载、展示四种状态、筛选参数进入请求、分页生效，并验证“导出全部余料”触发文件下载。

- [ ] **Step 2: 验证测试失败**

Run: `npm --prefix frontend run test:e2e -- tests/e2e/remnant-inventory/global.spec.ts`

Expected: 页签和按钮不存在。

- [ ] **Step 3: 实现 API 和全局面板**

增加全局查询类型和 API；面板使用紧凑筛选表单、服务端分页表格以及导出按钮。通过 `onOpenDetail` 回调复用页面现有详情抽屉，不复制库存状态操作。

- [ ] **Step 4: 验证页面测试和构建通过**

Run: `npm --prefix frontend run test:e2e -- tests/e2e/remnant-inventory/global.spec.ts`

Run: `npm --prefix frontend run build`

Expected: 测试和构建全部通过。

- [ ] **Step 5: 提交全局页面**

```powershell
git add frontend/src/features/remnant-inventory frontend/tests/e2e/remnant-inventory/global.spec.ts
git commit -m "feat(frontend): add global remnant inventory export"
```

### Task 6: 回归验证与文档同步

**Files:**
- Modify: `frontend/src/features/remnant-inventory/README.md`

**Interfaces:**
- Produces: 面向维护者的最终功能和接口说明。

- [ ] **Step 1: 更新模块说明**

记录扩展候选、短中文忽略、多零件确认、全局查询和导出接口。

- [ ] **Step 2: 运行解析、后端和前端完整验证**

Run: `uv run --project Stages/remnant_drawing_reader pytest Stages/remnant_drawing_reader/tests -q`

Run: `uv run --project backend pytest backend/tests/remnant_inventory -q`

Run: `uv run --project backend ruff check backend/app/modules/remnant_inventory backend/tests/remnant_inventory`

Run: `npm --prefix frontend run build`

Expected: 所有命令退出码为 0。

- [ ] **Step 3: 对精武路语料运行报告**

Run: `uv run --project backend python scripts/remnant_inventory/report_corpus.py --input "C:\Users\Ran-xin\Desktop\kuak\余料库\手动拆分清单\余料精武路\dxf"`

Expected: 新格式材质、零件和中文标题进入候选，2～3 字中文标注不产生 `UNRECOGNIZED_TEXT`。

- [ ] **Step 4: 提交文档**

```powershell
git add frontend/src/features/remnant-inventory/README.md
git commit -m "docs(remnants): document global inventory workflow"
```

- [ ] **Step 5: 完成代码审查和最终验收**

使用 `requesting-code-review` 和 `verification-before-completion` 检查需求覆盖、回归结果、Excel 内容和工作区状态；发现问题必须先补失败测试再修复。
