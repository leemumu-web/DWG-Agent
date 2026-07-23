import { expect, test, type Route } from '@playwright/test';

const now = '2026-07-22T10:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'materials-e2e', timestamp: now } });
const admin = {
  id: 1, username: 'admin', real_name: '管理员', status: 'active',
  roles: [{ id: 1, code: 'admin', name: '系统管理员', is_system: true }],
  created_at: now, updated_at: now,
};
const disabled = {
  id: 2, code: 'Q355D', family_code: 'Q355', enabled: false, aliases: ['旧-Q355D'],
  created_at: now, updated_at: now,
};

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(envelope(data)) });
}

test('material status switch confirms and re-enables the row', async ({ page }) => {
  let enabled = false;
  let allCatalogRequests = 0;
  let enabledCatalogRequests = 0;
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, { access_token: 'e2e-token', user: admin }, 201));
  await page.route('**/api/v1/remnant-materials', (route) => {
    enabledCatalogRequests += 1;
    return json(route, enabled ? [{ ...disabled, enabled }] : []);
  });
  await page.route('**/api/v1/remnant-materials?enabled_only=false', (route) => {
    allCatalogRequests += 1;
    return json(route, [{ ...disabled, enabled }]);
  });
  await page.route('**/api/v1/remnant-materials/2', async (route) => {
    expect(route.request().method()).toBe('PATCH');
    expect(route.request().postDataJSON()).toEqual({ enabled: true });
    enabled = true;
    await json(route, { ...disabled, enabled: true });
  });

  await page.goto('/remnants?tab=materials');
  await expect(page.getByText('Q355D', { exact: true })).toBeVisible();
  await page.getByRole('switch', { name: 'Q355D 启用状态' }).click();
  await expect(page.getByText('重新启用 Q355D？')).toBeVisible();
  await page.getByRole('button', { name: '确 定' }).click();
  await expect.poll(() => enabled).toBe(true);
  await expect(page.getByRole('switch', { name: 'Q355D 启用状态' })).toBeChecked();
  await expect.poll(() => allCatalogRequests).toBeGreaterThan(1);
  await expect.poll(() => enabledCatalogRequests).toBeGreaterThan(1);
  await expect(page.getByRole('button', { name: '停用' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '编辑' })).toBeVisible();
});

test('material status switch restores its value and shows a Chinese error on failure', async ({ page }) => {
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, { access_token: 'e2e-token', user: admin }, 201));
  await page.route('**/api/v1/remnant-materials', (route) => json(route, []));
  await page.route('**/api/v1/remnant-materials?enabled_only=false', (route) => json(route, [disabled]));
  await page.route('**/api/v1/remnant-materials/2', (route) => route.fulfill({
    status: 409,
    contentType: 'application/json',
    body: JSON.stringify({
      error: { code: 'REMNANT_MATERIAL_CONFLICT', message: '该材质当前无法启用。' },
      meta: envelope(null).meta,
    }),
  }));

  await page.goto('/remnants?tab=materials');
  const statusSwitch = page.getByRole('switch', { name: 'Q355D 启用状态' });
  await expect(statusSwitch).not.toBeChecked();
  await statusSwitch.click();
  await page.getByRole('button', { name: '确 定' }).click();
  await expect(page.getByText('该材质当前无法启用。')).toBeVisible();
  await expect(statusSwitch).not.toBeChecked();
});
