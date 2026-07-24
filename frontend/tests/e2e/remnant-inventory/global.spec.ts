import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-23T10:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'remnant-global-e2e', timestamp: now } });
const user = { id: 8, username: 'worker', real_name: '余料工人', status: 'active', roles: [{ id: 8, code: 'remnant_worker', name: '余料工人', is_system: true }], created_at: now, updated_at: now };

async function json(route: Route, data: unknown) {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
}

function remnant(id: number, status: string, importedBy = 8) {
  return {
    id, source_file_id: 100 + id, dxf_file_id: 200 + id, source_name: `余料-${id}.dxf`, source_ext: '.dxf',
    thickness_mm: `${id}0.000`, material_id: 1, material_code: 'Q355B', project_no: `精武路项目-${id}`,
    parts: [`JWL-${id}01-1`], status, imported_by: importedBy, reserved_by: status === 'reserved' ? 8 : null,
    reserved_by_name: status === 'reserved' ? '余料工人' : null, reserved_at: status === 'reserved' ? now : null,
    used_by: status === 'used' ? 8 : null, used_at: status === 'used' ? now : null,
    version: 1, created_at: now, updated_at: now,
  };
}

async function mockGlobal(page: Page, options: { bulkValidationError?: boolean } = {}) {
  const requestUrls: string[] = [];
  const bulkRequests: number[][] = [];
  const rows = [
    remnant(1, 'available'),
    remnant(2, 'reserved'),
    remnant(3, 'used'),
    remnant(4, 'archived'),
    remnant(5, 'available'),
    remnant(6, 'available', 99),
  ];
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
  await page.route('**/api/v1/remnants/bulk-archive', async (route) => {
    const body = route.request().postDataJSON() as { remnant_ids: number[] };
    bulkRequests.push(body.remnant_ids);
    if (options.bulkValidationError) {
      return route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'VALIDATION_ERROR', message: 'Request validation failed.',
            details: { errors: [{ type: 'too_long', loc: ['body', 'remnant_ids'], msg: 'List should have at most 200 items' }] },
          },
          meta: envelope(null).meta,
        }),
      });
    }
    rows.find((row) => row.id === 1)!.status = 'archived';
    rows.find((row) => row.id === 5)!.status = 'reserved';
    await json(route, envelope({
      archived: [1],
      failed: [{ remnant_id: 5, code: 'REMNANT_LOCKED', message: '只有状态为“可用”的余料才能归档。' }],
    }));
  });
  await page.route('**/api/v1/remnants/all*', async (route) => {
    requestUrls.push(route.request().url());
    const url = new URL(route.request().url());
    const statuses = url.searchParams.getAll('statuses');
    const filteredRows = statuses.length ? rows.filter((row) => statuses.includes(row.status)) : rows;
    await json(route, {
      data: filteredRows,
      pagination: { page: Number(new URL(route.request().url()).searchParams.get('page') ?? 1), page_size: 20, total: 21, total_pages: 2 },
      meta: { request_id: 'remnant-global-e2e', timestamp: now },
    });
  });
  return { requestUrls, bulkRequests };
}

test('workers browse filter page and export the complete remnant inventory', async ({ page }) => {
  const state = await mockGlobal(page);

  await page.goto('/remnants?tab=global');

  await expect(page.getByRole('tab', { name: '全部余料' })).toHaveAttribute('aria-selected', 'true');
  const table = page.getByRole('table');
  await expect(table.getByText('可用', { exact: true }).first()).toBeVisible();
  await expect(table.getByText('已预占', { exact: true })).toBeVisible();
  await expect(table.getByText('已使用', { exact: true })).toHaveCount(0);
  await expect(table.getByText('已归档', { exact: true })).toHaveCount(0);
  await expect.poll(() => state.requestUrls.length).toBe(1);
  const initialUrl = new URL(state.requestUrls[0]);
  expect(initialUrl.searchParams.getAll('statuses')).toEqual(['available', 'reserved']);

  await page.getByLabel('项目编号一筛选').fill('精武路');
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

test('bulk archive validation errors show only Chinese field and reason', async ({ page }) => {
  await mockGlobal(page, { bulkValidationError: true });
  await page.goto('/remnants?tab=global');
  await page.getByRole('checkbox', { name: '选择余料 1' }).check();
  await page.getByRole('button', { name: '批量归档' }).click();
  await page.getByRole('button', { name: '确 定' }).click();

  await expect(page.getByText('请求参数错误：余料数量超过限制')).toBeVisible();
  await expect(page.getByText('List should have at most 200 items')).toHaveCount(0);
  await expect(page.getByText('VALIDATION_ERROR')).toHaveCount(0);
});

test('workers reveal history and keep only failed rows selected after partial batch archive', async ({ page }) => {
  const state = await mockGlobal(page);

  await page.goto('/remnants?tab=global');
  await page.getByRole('switch', { name: '显示历史余料' }).click();

  const table = page.getByRole('table');
  await expect(table.getByText('已使用', { exact: true })).toBeVisible();
  await expect(table.getByText('已归档', { exact: true })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: '选择余料 2' })).toBeDisabled();
  await expect(page.getByRole('checkbox', { name: '选择余料 6' })).toBeDisabled();

  await page.getByRole('checkbox', { name: '选择余料 1' }).check();
  await page.getByRole('checkbox', { name: '选择余料 5' }).check();
  await page.getByRole('button', { name: '批量归档' }).click();
  await page.getByRole('button', { name: '确 定' }).click();

  await expect.poll(() => state.bulkRequests).toEqual([[1, 5]]);
  await expect(page.getByText('已归档 1 张，1 张未处理')).toBeVisible();
  await expect(page.getByText('余料 #5：只有状态为“可用”的余料才能归档。')).toBeVisible();
  await expect(page.getByRole('checkbox', { name: '选择余料 1' })).not.toBeChecked();
  await expect(page.getByRole('checkbox', { name: '选择余料 5' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: '选择余料 5' })).toBeDisabled();
  await page.getByRole('button', { name: 'Close' }).click();
  await expect(page.getByText('已归档 1 张，1 张未处理')).toHaveCount(0);
});

test('changing filters or history visibility clears selected remnants', async ({ page }) => {
  await mockGlobal(page);

  await page.goto('/remnants?tab=global');
  const first = page.getByRole('checkbox', { name: '选择余料 1' });
  const archiveButton = page.getByRole('button', { name: '批量归档' });

  await first.check();
  await expect(archiveButton).toBeEnabled();
  await page.getByLabel('项目编号一筛选').fill('精武路');
  await page.getByRole('button', { name: '查询全部余料' }).click();
  await expect(first).not.toBeChecked();
  await expect(archiveButton).toBeDisabled();

  await first.check();
  await page.getByRole('switch', { name: '显示历史余料' }).click();
  await expect(first).not.toBeChecked();
  await expect(archiveButton).toBeDisabled();
});
