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

test('material management lists disabled entries and can re-enable them', async ({ page }) => {
  let enabled = false;
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, { access_token: 'e2e-token', user: admin }, 201));
  await page.route('**/api/v1/remnant-materials', (route) => json(route, []));
  await page.route('**/api/v1/remnant-materials?enabled_only=false', (route) => json(route, [{ ...disabled, enabled }]));
  await page.route('**/api/v1/remnant-materials/2', async (route) => {
    expect(route.request().method()).toBe('PATCH');
    expect(route.request().postDataJSON()).toEqual({ enabled: true });
    enabled = true;
    await json(route, { ...disabled, enabled: true });
  });

  await page.goto('/remnants?tab=materials');
  await expect(page.getByText('Q355D', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '启用' }).click();
  await page.getByRole('button', { name: '确 定' }).click();
  await expect.poll(() => enabled).toBe(true);
  await expect(page.getByRole('button', { name: '停用' })).toBeVisible();
});
