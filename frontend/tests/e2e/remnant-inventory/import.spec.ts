import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-22T10:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'remnant-import-e2e', timestamp: now } });
const user = { id: 8, username: 'worker', real_name: '余料工人', status: 'active', roles: [{ id: 8, code: 'remnant_worker', name: '余料工人', is_system: true }], created_at: now, updated_at: now };

function item(id: number, name: string, status: string) {
  return {
    id, batch_id: 77, source_file_id: 500 + id, dxf_file_id: status === 'failed' ? null : 600 + id,
    original_name: name, source_ext: name.endsWith('.dwg') ? '.dwg' : '.dxf', attempt: 1, status,
    material_candidates: [{ value: 'Q235B', evidence: [{ raw_text: '材质: Q235B', entity_type: 'TEXT', layer: 'TITLE', block_path: [] }] }],
    project_candidates: [{ value: `PJ-${id}`, evidence: [{ raw_text: `项目编号: PJ-${id}`, entity_type: 'TEXT', layer: 'TITLE', block_path: [] }] }],
    part_candidates: [{ value: `L-${id}`, evidence: [] }], warnings: id === 1 ? [{ code: 'MATERIAL_CANDIDATES_CONFLICT', message: '发现多个材质候选，请人工确认' }] : [],
    thickness_mm: null, material_id: null, project_no: null, parts: [],
    error_code: status === 'failed' ? 'REMNANT_PARSE_FAILED' : null,
    error_message: status === 'failed' ? '图纸解析失败' : null,
  };
}

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(envelope(data)) });
}

async function mockImport(page: Page) {
  const items = [item(1, '现场余料-A.dwg', 'pending_confirmation'), item(2, '现场余料-B.dxf', 'pending_confirmation'), item(3, '待重试.dwg', 'failed')];
  let retryCalls = 0;
  let patchCalls = 0;
  const batch = () => ({
    id: 77, created_by: 8, status: 'awaiting_confirmation', total_count: 3,
    converting_count: 0, parsing_count: 0, pending_count: items.filter((row) => row.status === 'pending_confirmation').length,
    confirmed_count: items.filter((row) => row.status === 'confirmed').length, failed_count: 1, cancelled_count: 0,
    items, created_at: now, updated_at: now,
  });
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, { access_token: 'e2e-token', user }, 201));
  await page.route('**/api/v1/remnant-materials', (route) => json(route, [{ id: 1, code: 'Q235B', family_code: 'Q235', enabled: true, created_at: now, updated_at: now }]));
  await page.route('**/api/v1/remnant-import-batches', async (route) => {
    expect(route.request().method()).toBe('POST');
    expect((await route.request().postDataBuffer())?.length).toBeGreaterThan(0);
    await json(route, batch(), 202);
  });
  await page.route('**/api/v1/remnant-import-batches/77', (route) => json(route, batch()));
  await page.route('**/api/v1/remnant-import-items/3/retry', async (route) => { retryCalls += 1; await json(route, { item_id: 3, attempt: 2 }, 202); });
  await page.route('**/api/v1/remnant-import-batches/77/bulk-thickness', async (route) => {
    const payload = route.request().postDataJSON();
    for (const selected of items.filter((row) => payload.item_ids.includes(row.id))) selected.thickness_mm = String(payload.thickness_mm);
    await json(route, { updated_item_ids: payload.item_ids });
  });
  await page.route('**/api/v1/remnant-import-items/1', async (route) => {
    patchCalls += 1;
    const payload = route.request().postDataJSON();
    Object.assign(items[0], { thickness_mm: payload.thickness_mm, material_id: payload.material_id, project_no: payload.project_no, parts: payload.parts });
    await json(route, items[0]);
  });
  await page.route('**/api/v1/remnant-import-items/bulk-confirm', async (route) => {
    expect(route.request().postDataJSON().item_ids.sort()).toEqual([1, 2]);
    items[0].status = 'confirmed';
    await json(route, { confirmed: [{ item_id: 1, remnant_id: 901 }], invalid: [{ item_id: 2, code: 'REMNANT_PROJECT_REQUIRED' }], already_confirmed: [] });
  });
  return { retryCalls: () => retryCalls, patchCalls: () => patchCalls };
}

test('mixed batch upload, refresh recovery, retry, bulk thickness, edit and partial confirmation', async ({ page }) => {
  const state = await mockImport(page);
  await page.goto('/remnants?tab=import');
  const chooser = page.locator('input[type=file]');
  await chooser.setInputFiles([
    { name: '现场余料-A.dwg', mimeType: 'application/acad', buffer: Buffer.from('AC1032 drawing A') },
    { name: '现场余料-B.dxf', mimeType: 'application/dxf', buffer: Buffer.from('0\nSECTION\n0\nEOF\n') },
    { name: '待重试.dwg', mimeType: 'application/acad', buffer: Buffer.from('AC1032 drawing C') },
  ]);
  await expect(page.getByText('现场余料-A.dwg').first()).toBeVisible();
  await expect(page.getByText('现场余料-B.dxf')).toBeVisible();
  await page.getByRole('button', { name: /批量导入/ }).click();
  await expect(page).toHaveURL(/tab=import.*batch=77/);
  await expect(page.getByText('批次 #77')).toBeVisible();

  await page.reload();
  await expect(page.getByText('现场余料-A.dwg').first()).toBeVisible();
  await page.getByRole('button', { name: '重试' }).click();
  await expect.poll(state.retryCalls).toBe(1);

  const confirmation = page.locator('.remnant-confirm-card');
  const checkboxes = confirmation.locator('.ant-checkbox-input');
  await checkboxes.nth(1).check();
  await checkboxes.nth(2).check();
  await confirmation.getByRole('button', { name: '批量填写厚度' }).click();
  await page.getByLabel('批量厚度').fill('10');
  await page.getByRole('dialog', { name: '批量填写厚度' }).getByRole('button', { name: '确 定' }).click({ force: true });
  await expect(confirmation.getByText('10 mm').first()).toBeVisible();

  await confirmation.getByRole('button', { name: '编辑' }).first().click();
  const editor = page.getByRole('dialog', { name: '确认 现场余料-A.dwg' });
  await expect(editor.getByText('MATERIAL_CANDIDATES_CONFLICT')).toBeVisible();
  await expect(editor.getByText(/TITLE: 材质: Q235B/)).toBeVisible();
  await editor.getByLabel('厚度（mm）').fill('10');
  await editor.getByLabel('标准材质').click();
  await page.getByText('Q235B', { exact: true }).last().click();
  await editor.getByLabel('项目编号').fill('PJ-CONFIRMED');
  await editor.getByLabel('零件编号（逗号、顿号或换行分隔）').fill('L-1、L-2');
  await editor.getByRole('button', { name: '确 定' }).click({ force: true });
  await expect.poll(state.patchCalls).toBe(1);

  await confirmation.getByRole('button', { name: '确认选中项' }).click();
  await expect(page.getByText('已确认 1 张，1 张需补充字段')).toBeVisible();
});
