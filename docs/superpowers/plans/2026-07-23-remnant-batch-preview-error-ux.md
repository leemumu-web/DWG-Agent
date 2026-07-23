# Remnant Batch Processing and Preview UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为余料库增加安全的批量归档与历史显示开关，并修复解析确认、在线预览、新材质录入、中文错误和材质启用开关的工人端体验。

**Architecture:** 保持现有 FastAPI/SQLAlchemy 模块和 React/Ant Design 页面边界；后端新增逐条保存点隔离的批量归档服务与接口，前端在全局余料面板消费结果。预览继续复用共享 `DxfPreviewModal`，错误中文化限定在余料库，通过稳定内部代码到中文文案的展示层映射完成。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、Pydantic、pytest、React 19、TypeScript、Ant Design 6、TanStack Query、react-zoom-pan-pinch、Playwright。

## Global Constraints

- 本期批量操作仅包含批量归档，不增加批量预留、释放或领用。
- 工人只能归档自己导入且状态为 `available` 的余料；管理员可归档任意 `available` 余料。
- 批量归档允许部分成功；单条失败不得回滚其他合法记录。
- “显示历史余料”默认关闭；关闭时显示 `available,reserved`，打开时显示全部四种状态。
- 中文化只覆盖整个余料库；稳定 `REMNANT_*` 错误代码保留但不得直接显示给工人。
- 不修改数据库结构，不新增迁移，不改变 Excel 全量导出的状态范围。
- 保留现有多进程部署方式，不引入进程内共享状态。

---

### Task 1: 后端批量归档领域服务

**Files:**
- Modify: `backend/app/modules/remnant_inventory/inventory.py`
- Test: `backend/tests/remnant_inventory/test_inventory.py`

**Interfaces:**
- Consumes: 现有 `archive_remnant(db, remnant_id, actor)` 权限、锁和审计规则。
- Produces: `BulkArchiveFailure(remnant_id: int, code: str, message: str)`、`BulkArchiveResult(archived: list[int], failed: list[BulkArchiveFailure])`、`bulk_archive_remnants(db, remnant_ids: Sequence[int], actor: User) -> BulkArchiveResult`。

- [ ] **Step 1: 写服务层失败测试**

在 `test_inventory.py` 增加以下场景：工人自己的可用余料成功、他人的余料失败、已预留余料失败、管理员可归档他人余料、重复编号只处理一次、不存在编号返回失败且不影响其他项，并断言成功记录均生成 `remnants.archive` 审计日志。

```python
def test_bulk_archive_partially_succeeds_and_preserves_input_order(db) -> None:
    from app.modules.operations.audit.models import AuditLog
    from app.modules.remnant_inventory.inventory import bulk_archive_remnants

    owner = _user(db, "bulk-archive-owner")
    outsider = _user(db, "bulk-archive-outsider")
    own = _remnant(db, owner=owner, suffix="a")
    foreign = _remnant(db, owner=outsider, suffix="b")
    reserved = _remnant(db, owner=owner, suffix="c", status="reserved")

    result = bulk_archive_remnants(
        db, [own.id, foreign.id, reserved.id, own.id, 999999], actor=owner
    )

    assert result.archived == [own.id]
    assert [(item.remnant_id, item.code) for item in result.failed] == [
        (foreign.id, "REMNANT_ARCHIVE_FORBIDDEN"),
        (reserved.id, "REMNANT_LOCKED"),
        (999999, "REMNANT_NOT_FOUND"),
    ]
    assert db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "remnants.archive",
            AuditLog.resource_id == own.id,
        )
    ) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/remnant_inventory/test_inventory.py -k bulk_archive -q`

Expected: FAIL，提示 `bulk_archive_remnants` 尚不存在。

- [ ] **Step 3: 实现逐条保存点处理**

在 `inventory.py` 从 `fastapi` 导入 `HTTPException`，增加结果类型和服务。使用首次出现顺序去重；每条调用现有 `archive_remnant`，以 `db.begin_nested()` 隔离失败；捕获 `HTTPException` 后读取 `detail.code/detail.message` 写入失败项。不要在服务内部提交外层事务。同时把本任务直接使用的三条 message 固定为中文：`REMNANT_NOT_FOUND`→“余料不存在或已被删除。”、`REMNANT_LOCKED`→“只有状态为‘可用’的余料才能归档。”、`REMNANT_ARCHIVE_FORBIDDEN`→“只能归档自己导入的余料。”；其余余料错误在 Task 5 集中处理。

```python
@dataclass(frozen=True)
class BulkArchiveFailure:
    remnant_id: int
    code: str
    message: str


@dataclass(frozen=True)
class BulkArchiveResult:
    archived: list[int]
    failed: list[BulkArchiveFailure]


def bulk_archive_remnants(
    db: Session, remnant_ids: Sequence[int], *, actor: User
) -> BulkArchiveResult:
    _require_user(actor)
    result = BulkArchiveResult(archived=[], failed=[])
    for remnant_id in dict.fromkeys(remnant_ids):
        try:
            with db.begin_nested():
                archive_remnant(db, remnant_id, actor=actor)
            result.archived.append(remnant_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            result.failed.append(BulkArchiveFailure(
                remnant_id=remnant_id,
                code=str(detail.get("code", "REMNANT_ARCHIVE_FAILED")),
                message=str(detail.get("message", "余料归档失败。")),
            ))
    return result
```

- [ ] **Step 4: 运行服务层测试**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/remnant_inventory/test_inventory.py -q`

Expected: PASS。

- [ ] **Step 5: 提交服务层变更**

```powershell
git add backend/app/modules/remnant_inventory/inventory.py backend/tests/remnant_inventory/test_inventory.py
git commit -m "feat(remnants): add partial bulk archive service"
```

---

### Task 2: 批量归档 API 合同

**Files:**
- Modify: `backend/app/modules/remnant_inventory/schemas.py`
- Modify: `backend/app/modules/remnant_inventory/routes.py`
- Test: `backend/tests/remnant_inventory/test_api.py`

**Interfaces:**
- Consumes: Task 1 的 `bulk_archive_remnants`。
- Produces: `POST /api/v1/remnants/bulk-archive`，请求 `{ "remnant_ids": number[] }`，响应 `{ archived: number[], failed: { remnant_id, code, message }[] }`。

- [ ] **Step 1: 写 API 合同失败测试**

测试 1–200 条边界、201 条返回 422、空数组返回 422、普通工人部分成功、管理员成功，以及响应失败原因均为中文。

```python
def test_worker_bulk_archive_returns_success_and_chinese_failure_details(
    client, worker_headers
) -> None:
    own_id, foreign_id = _seed_bulk_archive_rows()
    response = client.post(
        "/api/v1/remnants/bulk-archive",
        headers=worker_headers,
        json={"remnant_ids": [own_id, foreign_id]},
    )
    assert response.status_code == 200
    assert response.json()["data"]["archived"] == [own_id]
    assert response.json()["data"]["failed"] == [{
        "remnant_id": foreign_id,
        "code": "REMNANT_ARCHIVE_FORBIDDEN",
        "message": "只能归档自己导入的余料。",
    }]
```

- [ ] **Step 2: 运行 API 测试确认失败**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/remnant_inventory/test_api.py -k bulk_archive -q`

Expected: FAIL，接口返回 404/405。

- [ ] **Step 3: 增加请求与响应模型及路由**

```python
class RemnantBulkArchiveRequest(BaseModel):
    remnant_ids: list[int] = Field(min_length=1, max_length=200)


@remnants_router.post("/bulk-archive")
def post_bulk_archive(
    payload: RemnantBulkArchiveRequest,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    result = bulk_archive_remnants(db, payload.remnant_ids, actor=current_user)
    db.commit()
    return ok({
        "archived": result.archived,
        "failed": [asdict(item) for item in result.failed],
    }, request.state.request_id)
```

路由必须放在 `/{remnant_id}` 之前，避免 `bulk-archive` 被动态路径解析；Pydantic 响应模型可用于固定字段结构。

- [ ] **Step 4: 运行 API 与余料模块测试**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/remnant_inventory/test_api.py backend/tests/remnant_inventory/test_inventory.py -q`

Expected: PASS。

- [ ] **Step 5: 提交 API 变更**

```powershell
git add backend/app/modules/remnant_inventory/schemas.py backend/app/modules/remnant_inventory/routes.py backend/tests/remnant_inventory/test_api.py
git commit -m "feat(remnants): expose bulk archive endpoint"
```

---

### Task 3: 全局余料历史开关与批量归档界面

**Files:**
- Modify: `frontend/src/features/remnant-inventory/types.ts`
- Modify: `frontend/src/features/remnant-inventory/api.ts`
- Modify: `frontend/src/features/remnant-inventory/RemnantGlobalPanel.tsx`
- Modify: `frontend/src/features/remnant-inventory/RemnantInventoryPage.tsx`
- Test: `frontend/tests/e2e/remnant-inventory/global.spec.ts`

**Interfaces:**
- Consumes: Task 2 的批量接口、现有全局列表接口、当前用户 `id` 和 `isAdmin`。
- Produces: `bulkArchiveRemnants(remnantIds: number[]): Promise<BulkArchiveResult>`；`RemnantGlobalPanel` 新增 `currentUserId?: number`、`isAdmin: boolean` 属性。

- [ ] **Step 1: 扩展 Playwright 失败测试**

在 mock 中根据 `statuses` 参数过滤历史行，准备两条属于当前工人的可用余料，并让批量接口模拟其中一条因并发状态变化失败。断言默认不显示已使用/已归档；打开开关后显示全部；他人或非可用行不能勾选；部分失败后失败行仍选中并出现中文提示。

```ts
await expect(page.getByText('已使用', { exact: true })).toHaveCount(0);
await page.getByRole('switch', { name: '显示历史余料' }).click();
await expect(page.getByText('已归档', { exact: true })).toBeVisible();

await page.getByRole('checkbox', { name: '选择余料 1' }).check();
await page.getByRole('checkbox', { name: '选择余料 5' }).check();
await page.getByRole('button', { name: '批量归档' }).click();
await page.getByRole('button', { name: '确 定' }).click();
await expect(page.getByText('已归档 1 张，1 张未处理')).toBeVisible();
```

- [ ] **Step 2: 运行端到端测试确认失败**

Run: `cd frontend; npm exec playwright test tests/e2e/remnant-inventory/global.spec.ts`

Expected: FAIL，历史开关或批量归档按钮不存在。

- [ ] **Step 3: 增加类型与 API 客户端**

```ts
export interface BulkArchiveResult {
  archived: number[];
  failed: Array<{ remnant_id: number; code: string; message: string }>;
}

export async function bulkArchiveRemnants(remnantIds: number[]): Promise<BulkArchiveResult> {
  const response = await apiClient.post<ApiEnvelope<BulkArchiveResult>>(
    '/api/v1/remnants/bulk-archive',
    { remnant_ids: remnantIds },
  );
  return response.data.data;
}
```

- [ ] **Step 4: 实现历史开关和行选择**

将初始状态改为 `statuses: ['available', 'reserved']`，增加 `showHistory`。开关变化时直接设置全部/活动状态并重置 `page: 1`。表格增加受控 `rowSelection`；`getCheckboxProps` 仅允许 `available && (isAdmin || row.imported_by === currentUserId)`，并提供 `aria-label: 选择余料 ${row.id}`。批量按钮使用 `Popconfirm`，成功后清除成功编号、保留失败编号、刷新 `['remnants', 'all']`。在表格上方保存并渲染一个可关闭的 warning Alert：标题为“已归档 N 张，M 张未处理”，内容逐条显示“余料 #编号：中文原因”；下一次批量提交时替换旧结果。

```tsx
<Switch
  aria-label="显示历史余料"
  checked={showHistory}
  checkedChildren="显示历史"
  unCheckedChildren="隐藏历史"
  onChange={(checked) => {
    setShowHistory(checked);
    setSearch((current) => ({
      ...current,
      statuses: checked ? allStatuses : activeStatuses,
      page: 1,
    }));
  }}
/>
```

- [ ] **Step 5: 运行端到端测试与前端构建**

Run: `cd frontend; npm exec playwright test tests/e2e/remnant-inventory/global.spec.ts; npm run build`

Expected: 测试 PASS，TypeScript 和 Vite 构建 PASS。

- [ ] **Step 6: 提交全局列表变更**

```powershell
git add frontend/src/features/remnant-inventory/types.ts frontend/src/features/remnant-inventory/api.ts frontend/src/features/remnant-inventory/RemnantGlobalPanel.tsx frontend/src/features/remnant-inventory/RemnantInventoryPage.tsx frontend/tests/e2e/remnant-inventory/global.spec.ts
git commit -m "feat(frontend): batch archive global remnants"
```

---

### Task 4: 解析确认布局与无候选材质创建

**Files:**
- Modify: `frontend/src/features/remnant-inventory/RemnantConfirmationPanel.tsx`
- Modify: `frontend/src/features/remnant-inventory/styles.css`
- Test: `frontend/tests/e2e/remnant-inventory/import.spec.ts`

**Interfaces:**
- Consumes: 现有 `resolveOrCreateRemnantMaterial(code)`；不新增后端接口。
- Produces: 无候选和未建档候选共用的“完整牌号”录入控件；固定右侧操作列。

- [ ] **Step 1: 写布局和无候选材质失败测试**

扩展 `mockImport` 支持 `materialCandidates: []`。断言编辑弹窗仍出现“新材质完整牌号”，输入 `Q500XYZ` 后调用创建接口并自动写入标准材质选择；给表格容器设置窄宽度后，操作列的编辑按钮仍在可视区域。

```ts
await editor.getByLabel('新材质完整牌号').fill('Q500XYZ');
await editor.getByRole('button', { name: '新建并使用 Q500XYZ' }).click();
await expect(editor.getByLabel('标准材质')).toHaveText(/Q500XYZ/);
await expect(confirmation.getByRole('button', { name: '编辑' }).first()).toBeVisible();
```

- [ ] **Step 2: 运行导入测试确认失败**

Run: `cd frontend; npm exec playwright test tests/e2e/remnant-inventory/import.spec.ts -g "无候选材质|操作列"`

Expected: FAIL，无候选时创建控件不存在或操作列越界。

- [ ] **Step 3: 抽取统一的新材质录入显示条件**

创建入口的显示条件改为 `!form.getFieldValue('material_id') || unmatchedMaterialCodes.length > 0`，无候选时输入框为空，未建档候选唯一时继续预填。保留请求竞态保护；创建成功后更新 `['remnant-materials']` 缓存并设置当前 `material_id`。

使用 `Form.useWatch('material_id', form)` 驱动显示，不以 `unmatchedMaterialCodes.length` 作为唯一条件。

- [ ] **Step 4: 固定操作列并启用横向滚动**

```tsx
<Table<RemnantImportItem>
  scroll={{ x: 1180 }}
  columns={[
    { title: '原始文件', dataIndex: 'original_name', width: 240, ellipsis: true },
    { title: '厚度', dataIndex: 'thickness_mm', width: 100 },
    { title: '材质候选', key: 'material', width: 200 },
    { title: '项目编号', key: 'project', width: 220 },
    { title: '零件数', key: 'parts', width: 90 },
    { title: '校验结果', key: 'validation', width: 190 },
    { title: '操作', key: 'actions', width: 160, fixed: 'right', render: (_, row) => (
      <Space>
        <Button type="link" icon={<EyeOutlined />} disabled={!row.dxf_file_id} onClick={() => setPreview(row)}>预览</Button>
        <Button type="link" icon={<EditOutlined />} disabled={row.status === 'confirmed'} onClick={() => edit(row)}>编辑</Button>
      </Space>
    ) },
  ]}
/>
```

在 `styles.css` 约束 `.remnant-confirm-card .ant-table-wrapper { min-width: 0; }`，不使用隐藏溢出裁剪操作列。

- [ ] **Step 5: 运行导入端到端测试与构建**

Run: `cd frontend; npm exec playwright test tests/e2e/remnant-inventory/import.spec.ts; npm run build`

Expected: PASS。

- [ ] **Step 6: 提交确认界面变更**

```powershell
git add frontend/src/features/remnant-inventory/RemnantConfirmationPanel.tsx frontend/src/features/remnant-inventory/styles.css frontend/tests/e2e/remnant-inventory/import.spec.ts
git commit -m "fix(frontend): keep remnant confirmation actions accessible"
```

---

### Task 5: 余料库中文错误与警告展示

**Files:**
- Modify: `backend/app/modules/remnant_inventory/imports.py`
- Modify: `backend/app/modules/remnant_inventory/inventory.py`
- Modify: `backend/app/modules/remnant_inventory/materials.py`
- Modify: `backend/app/modules/remnant_inventory/execution.py`
- Modify: `backend/app/modules/remnant_inventory/routes.py`
- Create: `frontend/src/features/remnant-inventory/errors.ts`
- Modify: `frontend/src/features/remnant-inventory/RemnantBatchProgress.tsx`
- Modify: `frontend/src/features/remnant-inventory/RemnantConfirmationPanel.tsx`
- Modify: `frontend/src/features/remnant-inventory/RemnantInventoryPage.tsx`
- Modify: `frontend/src/features/remnant-inventory/RemnantMaterialCatalog.tsx`
- Test: `backend/tests/remnant_inventory/test_api.py`
- Test: `frontend/tests/e2e/remnant-inventory/import.spec.ts`

**Interfaces:**
- Produces: `describeRemnantCode(code: string, fallback?: string): string` 和 `describeRemnantError(error: unknown, fallback: string): string`，余料模块统一使用。
- Stable codes: 所有 `REMNANT_*` 名称保持不变，仅后端 `message` 和前端展示改变。

- [ ] **Step 1: 写中文错误失败测试**

后端对不存在、无权限、状态冲突、材质停用、分页错误、导入格式错误断言 `error.message` 不含英文；前端确认缺少厚度时断言显示“请填写余料厚度”且页面不存在 `REMNANT_THICKNESS_REQUIRED`。

```ts
await page.getByRole('button', { name: '确认选中项' }).click();
await expect(page.getByText('请填写余料厚度')).toBeVisible();
await expect(page.getByText('REMNANT_THICKNESS_REQUIRED')).toHaveCount(0);
```

- [ ] **Step 2: 运行中英文测试确认失败**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/remnant_inventory/test_api.py -k chinese -q`

Run: `cd frontend; npm exec playwright test tests/e2e/remnant-inventory/import.spec.ts -g "中文"`

Expected: FAIL，当前仍返回或显示英文/内部代码。

- [ ] **Step 3: 中文化后端余料异常 message**

逐一保留现有 code 和 HTTP 状态，仅将 message 改为确定的中文文案；Task 1 已修改的三条保持不变。例如：

```python
raise AppHTTPException(404, "REMNANT_NOT_FOUND", "余料不存在或已被删除。")
raise AppHTTPException(409, "REMNANT_LOCKED", "只有状态为“可用”的余料才能归档。")
raise AppHTTPException(403, "REMNANT_ARCHIVE_FORBIDDEN", "只能归档自己导入的余料。")
raise AppHTTPException(409, "REMNANT_MATERIAL_DISABLED", "该材质已停用，请联系管理员重新启用。")
```

覆盖 `execution.py`、`imports.py`、`inventory.py`、`materials.py`、`routes.py` 中所有 `AppHTTPException`，并把 `execution.py` 持久化的 “Drawing processing failed.”、“Drawing parsing failed.” 改为明确中文。确保批量归档失败明细复用这些中文 message。

- [ ] **Step 4: 建立余料专用展示映射**

```ts
const REMNANT_MESSAGES: Record<string, string> = {
  REMNANT_THICKNESS_REQUIRED: '请填写余料厚度',
  REMNANT_MATERIAL_REQUIRED: '请选择或新建材质',
  REMNANT_PROJECT_REQUIRED: '请填写项目编号',
  REMNANT_PARTS_REQUIRED: '至少填写一个零件编号',
  REMNANT_DXF_REQUIRED: '缺少可用于确认的 DXF 图纸',
  REMNANT_IMPORT_ITEM_NOT_READY: '该图纸当前不能确认，请刷新后重试',
};

export function describeRemnantCode(code: string, fallback = '操作未完成'): string {
  return REMNANT_MESSAGES[code] ?? fallback;
}
```

为解析器 warning codes 建立中文标题映射；未知警告显示“图纸存在需要人工确认的问题”。`describeRemnantError` 先处理余料库 Pydantic 422 明细，再包装共享 `describeApiError` 并剥离界面中的 `[REMNANT_*]` 后缀；开发日志仍可记录原错误。

```ts
const FIELD_NAMES: Record<string, string> = {
  thickness_mm: '厚度', material_id: '材质', project_no: '项目编号',
  parts: '零件编号', remnant_ids: '余料',
};

export function describeRemnantError(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const issues = error.response?.data?.error?.details?.errors;
    if (Array.isArray(issues) && issues.length) {
      return `请求参数错误：${issues.slice(0, 3).map((issue) => {
        const field = String(issue.loc?.at(-1) ?? '请求');
        const reason = String(issue.type ?? '').includes('missing') ? '不能为空'
          : String(issue.type ?? '').includes('too_long') ? '数量超过限制'
            : '格式不正确';
        return `${FIELD_NAMES[field] ?? '请求内容'}${reason}`;
      }).join('；')}`;
    }
  }
  return describeApiError(error, fallback)
    .replace(/\s*\[REMNANT_[A-Z0-9_]+\]/g, '');
}
```

- [ ] **Step 5: 接入所有余料 mutation 和校验结果**

给批量厚度、保存确认、批量确认、库存操作、材质保存/开关、导入取消/重试、导出等 mutation 补齐 `onError`。校验结果列调用 `describeRemnantCode`，警告 Alert 的 `title` 使用中文映射；`RemnantBatchProgress` 的说明列优先按 `error_code` 映射，未知 code 使用已是中文的 `error_message`，仍为英文时显示“图纸处理失败，请重试或联系管理员”。Pydantic 422 使用中文字段映射（`thickness_mm`→厚度、`material_id`→材质、`project_no`→项目编号、`parts`→零件编号、`remnant_ids`→余料）和“不能为空/数量超限/格式不正确”等中文原因，不展示英文 `msg`。

- [ ] **Step 6: 运行余料后端与前端测试**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/remnant_inventory -q`

Run: `cd frontend; npm exec playwright test tests/e2e/remnant-inventory; npm run build`

Expected: 全部 PASS，页面断言中无原始 `REMNANT_*`。

- [ ] **Step 7: 提交中文化变更**

```powershell
git add backend/app/modules/remnant_inventory/execution.py backend/app/modules/remnant_inventory/imports.py backend/app/modules/remnant_inventory/inventory.py backend/app/modules/remnant_inventory/materials.py backend/app/modules/remnant_inventory/routes.py backend/tests/remnant_inventory/test_api.py frontend/src/features/remnant-inventory/errors.ts frontend/src/features/remnant-inventory/RemnantBatchProgress.tsx frontend/src/features/remnant-inventory/RemnantConfirmationPanel.tsx frontend/src/features/remnant-inventory/RemnantInventoryPage.tsx frontend/src/features/remnant-inventory/RemnantMaterialCatalog.tsx frontend/tests/e2e/remnant-inventory/import.spec.ts
git commit -m "fix(remnants): localize worker-facing errors"
```

---

### Task 6: 材质启用状态交互开关

**Files:**
- Modify: `frontend/src/features/remnant-inventory/RemnantMaterialCatalog.tsx`
- Test: `frontend/tests/e2e/remnant-inventory/materials.spec.ts`

**Interfaces:**
- Consumes: 现有 `updateRemnantMaterial(materialId, { enabled })`。
- Produces: 状态列直接切换、中文确认、行级 loading；移除操作列重复启用/停用按钮。

- [ ] **Step 1: 写交互失败测试**

断言点击 `Q355D` 行中的开关后显示“重新启用 Q355D？”；确认后 PATCH `{ enabled: true }`，开关变为选中；模拟 409 时开关保持原值并显示中文错误。

- [ ] **Step 2: 运行材质端到端测试确认失败**

Run: `cd frontend; npm exec playwright test tests/e2e/remnant-inventory/materials.spec.ts`

Expected: FAIL，当前 Switch 为 disabled。

- [ ] **Step 3: 实现受控确认开关**

维护 `pendingToggle?: RemnantMaterial`，Switch 的 `onChange` 只打开确认弹窗；确认后调用 mutation。`loading` 仅作用于当前材质；成功刷新 `['remnant-materials']` 与 `['remnant-materials','all']`，失败交给 Task 5 的 `describeRemnantError`。操作列仅保留编辑按钮。

```tsx
<Switch
  aria-label={`${row.code} 启用状态`}
  checked={row.enabled}
  loading={toggle.isPending && pendingToggle?.id === row.id}
  onChange={() => setPendingToggle(row)}
/>
```

- [ ] **Step 4: 运行材质测试和构建**

Run: `cd frontend; npm exec playwright test tests/e2e/remnant-inventory/materials.spec.ts; npm run build`

Expected: PASS。

- [ ] **Step 5: 提交材质开关变更**

```powershell
git add frontend/src/features/remnant-inventory/RemnantMaterialCatalog.tsx frontend/tests/e2e/remnant-inventory/materials.spec.ts
git commit -m "feat(frontend): make material status switch interactive"
```

---

### Task 7: 在线 DXF 预览简化与拖动范围修复

**Files:**
- Modify: `frontend/src/features/files/DxfPreviewModal.tsx`
- Modify: `frontend/src/features/files/DxfPreviewModal.css`
- Test: `frontend/tests/e2e/files/dxf-preview.spec.ts`

**Interfaces:**
- 保持 `DxfPreviewModalProps` 与后端预览响应不变。
- 移除仅展示的 telemetry UI，不删除响应字段，保证其他调用方兼容。

- [ ] **Step 1: 写预览失败测试**

拦截预览 metadata 与 SVG blob；断言不存在 `DXF 图形信息`、`SVG / AUTHENTICATED`、`Drawing telemetry`；画布占据弹窗主体；背景计算样式为浅色；缩放后拖动画布并断言 transform 位移变化；点击“适合窗口”恢复居中。

```ts
await expect(page.getByLabel('DXF 图形信息')).toHaveCount(0);
await expect(page.getByText('SVG / AUTHENTICATED')).toHaveCount(0);
await expect(page.locator('.dxf-preview-stage')).toHaveCSS('background-color', 'rgb(244, 247, 251)');
```

- [ ] **Step 2: 运行预览测试确认失败**

Run: `cd frontend; npm exec playwright test tests/e2e/files -g "DXF 在线预览"`

Expected: FAIL，侧栏和左下状态仍存在，背景仍为深色。

- [ ] **Step 3: 简化预览 DOM 与缩放配置**

删除 `aciColor`、`.dxf-preview-status` 和 `<aside>`；shell 改为单列。`TransformWrapper` 使用 `limitToBounds={false}`、`centerZoomedOut`、`alignmentAnimation={{ disabled: true }}`，保留 `minScale=0.08`、`maxScale=24`。图片尺寸继续由 SVG/图像比例提供，不把可拖动范围限制在固定 1200×900 容器边缘；适合窗口按钮调用 `centerView()` 或按容器计算后的 `resetTransform()`。

```tsx
<TransformWrapper
  initialScale={1}
  minScale={0.08}
  maxScale={24}
  limitToBounds={false}
  centerOnInit
  centerZoomedOut
  wheel={{ step: 0.12 }}
>
```

- [ ] **Step 4: 改为项目同风格浅色视觉**

将 modal、header、footer、stage、controls、loading/error 统一为白色与浅灰蓝色，主体 stage 使用 `#f4f7fb`，网格线使用低透明度蓝灰色；控制按钮沿用 Ant Design 默认浅色按钮。移除所有 sidebar/status 相关 CSS 和移动端双行布局。

- [ ] **Step 5: 运行预览测试、余料预览回归和构建**

Run: `cd frontend; npm exec playwright test tests/e2e/files tests/e2e/remnant-inventory; npm run build`

Expected: PASS。

- [ ] **Step 6: 提交预览变更**

```powershell
git add frontend/src/features/files/DxfPreviewModal.tsx frontend/src/features/files/DxfPreviewModal.css frontend/tests/e2e/files/dxf-preview.spec.ts
git commit -m "fix(frontend): expand and simplify dxf preview"
```

---

### Task 8: 文档同步与最终验收

**Files:**
- Modify: `frontend/src/features/remnant-inventory/README.md`
- Modify: `backend/app/modules/remnant_inventory/README.md`

**Interfaces:**
- 文档记录批量归档合同、权限、部分成功语义、历史开关默认值、新材质入口和中文错误边界。

- [ ] **Step 1: 更新模块文档**

后端 README 增加 `POST /api/v1/remnants/bulk-archive` 请求/响应示例和 200 条限制；前端 README 说明默认隐藏历史、无候选材质可创建、预览简化和材质开关交互。明确 Excel 导出仍包含全部状态。

- [ ] **Step 2: 运行静态检查和完整测试**

Run: `backend/.venv/Scripts/ruff.exe check backend/app/modules/remnant_inventory backend/tests/remnant_inventory`

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/remnant_inventory -q`

Run: `cd frontend; npm run build; npm exec playwright test tests/e2e/remnant-inventory tests/e2e/files`

Expected: Ruff PASS；后端余料测试 PASS；前端构建 PASS；余料与文件预览端到端测试 PASS。

- [ ] **Step 3: 重建本地验收环境**

Run: `docker compose --env-file .env.docker -f compose.yaml -f compose.dev.yaml build backend-api nginx`

Run: `docker compose --env-file .env.docker -f compose.yaml -f compose.dev.yaml --profile workers up -d backend-api nginx worker-remnant-convert worker-remnant-parse`

Expected: `backend-api`、`nginx`、`worker-remnant-convert`、`worker-remnant-parse` 均为 healthy，`http://127.0.0.1:8080/health/ready` 返回 200。

- [ ] **Step 4: 执行人工验收清单**

在 `http://127.0.0.1:8080/remnants` 验证：

1. 解析确认编辑按钮不越界。
2. 无材质候选时可创建并选中完整牌号。
3. 缺少厚度时显示“请填写余料厚度”。
4. 历史开关默认关闭，打开后显示已使用与已归档。
5. 批量归档部分成功时失败项保留且原因是中文。
6. 材质启用开关确认后即时刷新。
7. 在线预览为浅色全宽画布，无左下小字和右侧数据，缩放拖动范围正常。

- [ ] **Step 5: 提交文档并请求代码复审**

```powershell
git add frontend/src/features/remnant-inventory/README.md backend/app/modules/remnant_inventory/README.md
git commit -m "docs(remnants): document batch archive workflow"
```

使用 `requesting-code-review` 技能复审全部实现；修复所有 Critical/Important 问题后重新执行 Step 2–4，再报告完成。
