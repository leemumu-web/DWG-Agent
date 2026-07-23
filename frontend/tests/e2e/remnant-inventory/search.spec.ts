import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-22T10:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'remnant-search-e2e', timestamp: now } });
const user = {
  id: 8, username: 'worker', real_name: '余料工人', status: 'active',
  roles: [{ id: 8, code: 'remnant_worker', name: '余料工人', is_system: true }],
  created_at: now, updated_at: now,
};

const available = {
  id: 101, source_file_id: 501, dxf_file_id: 601, source_name: '可用余料.dwg', source_ext: '.dwg',
  thickness_mm: '10.000', material_id: 1, material_code: 'Q235B', project_no: 'PJ-A', parts: ['L-1', 'L-2'],
  status: 'available', imported_by: 8, reserved_by: null, reserved_by_name: null, reserved_at: null,
  used_by: null, used_at: null, version: 1, created_at: now, updated_at: now,
};
const reserved = {
  ...available, id: 102, source_name: '他人预占.dxf', source_ext: '.dxf', project_no: 'PJ-B', parts: ['L-9'],
  status: 'reserved', reserved_by: 9, reserved_by_name: '张师傅', reserved_at: now, version: 2,
};
const used = { ...available, id: 103, status: 'used', source_name: '历史余料.dwg', project_no: 'PJ-H' };

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(envelope(data)) });
}

async function mockSearch(page: Page) {
  let searchCalls = 0;
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, { access_token: 'e2e-token', user }, 201));
  await page.route('**/api/v1/remnant-materials', (route) => json(route, [{
    id: 1, code: 'Q235B', family_code: 'Q235', enabled: true, created_at: now, updated_at: now,
  }]));
  await page.route('**/api/v1/remnants?**', async (route) => {
    searchCalls += 1;
    const url = new URL(route.request().url());
    expect(url.searchParams.get('material_id')).toBe('1');
    expect(url.searchParams.get('thickness_mm')).toBe('10');
    expect(url.searchParams.get('include_family')).toBe('true');
    const rows = url.searchParams.getAll('statuses').includes('used') ? [used] : [available, reserved];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...envelope(rows), pagination: { page: 1, page_size: 20, total: rows.length, total_pages: 1 } }),
    });
  });
  await page.route('**/api/v1/remnants/101/reserve', (route) => route.fulfill({
    status: 409,
    contentType: 'application/json',
    body: JSON.stringify({ error: { code: 'REMNANT_ALREADY_RESERVED', message: '已被其他工人预占', details: { reserved_by: 9 } }, meta: envelope(null).meta }),
  }));
  await page.route('**/api/v1/remnants/101', (route) => json(route, available));
  await page.route('**/api/v1/remnants/102', (route) => json(route, reserved));
  return { searchCalls: () => searchCalls };
}

test('required exact filters, family expansion, active ordering and download permission', async ({ page }) => {
  const state = await mockSearch(page);
  await page.goto('/remnants');

  await page.getByRole('button', { name: '查询余料' }).click();
  await expect(page.getByText('请选择材质')).toBeVisible();
  await expect(page.getByText('请输入厚度')).toBeVisible();

  await page.getByLabel('标准材质').click();
  await page.getByText('Q235B', { exact: true }).click();
  await page.getByLabel('厚度（mm）').fill('10');
  await page.getByRole('switch').click();
  await page.getByRole('button', { name: '查询余料' }).click();

  const rows = page.locator('.ant-table-tbody tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText('可用');
  await expect(rows.nth(1)).toContainText('已预占');
  await expect(rows.nth(1)).toContainText('张师傅');

  await rows.nth(0).getByRole('button', { name: '详情' }).click();
  await page.getByRole('button', { name: '预占余料' }).click();
  await page.getByRole('button', { name: '确 定' }).click();
  await expect(page.getByText('已被其他工人预占')).toBeVisible();
  await expect.poll(state.searchCalls).toBeGreaterThan(1);
  await page.getByRole('button', { name: '关闭' }).click();

  await rows.nth(1).getByRole('button', { name: '详情' }).click();
  await expect(page.getByText('他人预占.dxf（DXF）')).toBeVisible();
  await expect(page.getByRole('button', { name: '下载原图 DXF' })).toBeDisabled();
  await expect(page.getByText('仍可在线预览，但不能下载原图或再次预占')).toBeVisible();
  await page.getByRole('button', { name: '关闭' }).click();

  const statusSelect = page.locator('.remnant-search-card .ant-select').nth(1).getByRole('combobox');
  await statusSelect.click();
  await statusSelect.press('ArrowDown');
  await statusSelect.press('ArrowDown');
  await statusSelect.press('Enter');
  await expect(page.locator('.remnant-search-card').getByText('已使用（历史）', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '查询余料' }).click();
  await expect(page.locator('.ant-table-tbody tr')).toHaveCount(1);
  await expect(page.locator('.ant-table-tbody tr').first()).toContainText('已使用');
});
