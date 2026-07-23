import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-23T10:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'remnant-global-e2e', timestamp: now } });
const user = { id: 8, username: 'worker', real_name: '余料工人', status: 'active', roles: [{ id: 8, code: 'remnant_worker', name: '余料工人', is_system: true }], created_at: now, updated_at: now };

async function json(route: Route, data: unknown) {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
}

function remnant(id: number, status: string) {
  return {
    id, source_file_id: 100 + id, dxf_file_id: 200 + id, source_name: `余料-${id}.dxf`, source_ext: '.dxf',
    thickness_mm: `${id}0.000`, material_id: 1, material_code: 'Q355B', project_no: `精武路项目-${id}`,
    parts: [`JWL-${id}01-1`], status, imported_by: 8, reserved_by: status === 'reserved' ? 8 : null,
    reserved_by_name: status === 'reserved' ? '余料工人' : null, reserved_at: status === 'reserved' ? now : null,
    used_by: status === 'used' ? 8 : null, used_at: status === 'used' ? now : null,
    version: 1, created_at: now, updated_at: now,
  };
}

async function mockGlobal(page: Page) {
  const requestUrls: string[] = [];
  const rows = [remnant(1, 'available'), remnant(2, 'reserved'), remnant(3, 'used'), remnant(4, 'archived')];
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, envelope({ access_token: 'e2e-token', user })));
  await page.route('**/api/v1/remnant-materials', (route) => json(route, envelope([
    { id: 1, code: 'Q355B', family_code: 'Q355', enabled: true, aliases: [], created_at: now, updated_at: now },
  ])));
  await page.route('**/api/v1/remnants/export.xlsx', (route) => route.fulfill({
    status: 200,
    contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    headers: { 'Content-Disposition': "attachment; filename*=UTF-8''%E4%BD%99%E6%96%99%E5%BA%93_20260723.xlsx" },
    body: Buffer.from('PK mock xlsx'),
  }));
  await page.route('**/api/v1/remnants/all*', async (route) => {
    requestUrls.push(route.request().url());
    await json(route, {
      data: rows,
      pagination: { page: Number(new URL(route.request().url()).searchParams.get('page') ?? 1), page_size: 20, total: 21, total_pages: 2 },
      meta: { request_id: 'remnant-global-e2e', timestamp: now },
    });
  });
  return { requestUrls };
}

test('workers browse filter page and export the complete remnant inventory', async ({ page }) => {
  const state = await mockGlobal(page);

  await page.goto('/remnants?tab=global');

  await expect(page.getByRole('tab', { name: '全部余料' })).toHaveAttribute('aria-selected', 'true');
  const table = page.getByRole('table');
  await expect(table.getByText('可用', { exact: true })).toBeVisible();
  await expect(table.getByText('已预占', { exact: true })).toBeVisible();
  await expect(table.getByText('已使用', { exact: true })).toBeVisible();
  await expect(table.getByText('已归档', { exact: true })).toBeVisible();
  await expect.poll(() => state.requestUrls.length).toBe(1);

  await page.getByLabel('项目编号筛选').fill('精武路');
  await page.getByLabel('零件编号筛选').fill('JWL-1');
  await page.getByRole('button', { name: '查询全部余料' }).click();
  await expect.poll(() => state.requestUrls.length).toBe(2);
  const filtered = new URL(state.requestUrls.at(-1)!);
  expect(filtered.searchParams.get('project')).toBe('精武路');
  expect(filtered.searchParams.get('part')).toBe('JWL-1');

  await page.getByRole('listitem', { name: '2' }).click();
  await expect.poll(() => state.requestUrls.length).toBe(3);
  expect(new URL(state.requestUrls.at(-1)!).searchParams.get('page')).toBe('2');

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出全部余料' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('余料库_20260723.xlsx');
});
