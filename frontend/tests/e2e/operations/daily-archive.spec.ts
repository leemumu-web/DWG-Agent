import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-20T01:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'daily-archive-e2e', timestamp: now } });
const pageEnvelope = (data: unknown[]) => ({ ...envelope(data), pagination: { page: 1, page_size: 10, total: data.length } });
const user = { id: 1, username: 'admin', real_name: '系统管理员', status: 'active', roles: [{ id: 1, code: 'super_admin', name: '系统管理员', is_system: true }], created_at: now, updated_at: now };

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(envelope(data)) });
}

async function openArchiveConsole(page: Page) {
  const overview = {
    status: 'ok',
    environment: { app_env: 'production', database_engine: 'mysql', database: 'dwg_agent', storage_backend: 'minio' },
    database: { status: 'ok' },
    storage: { status: 'ok', areas: [{ bucket: 'dwg-original', purpose_codes: ['source_dwg'] }], capacity: { status: 'ok', total_bytes: 1000, used_bytes: 100, free_bytes: 900, used_percent: 10, reason: null, checked_at: now } },
    catalog: { available_files: 12, deleted_files: 0, tracked_bytes: 8192 },
    transfers_today: { inbound_succeeded: 4, outbound_succeeded: 1, attention_required: 0 },
    latest_scan: null,
  };

  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, { access_token: 'archive-token', user }, 201));
  await page.route('**/api/v1/data-admin/overview', (route) => json(route, overview));
  await page.route('**/api/v1/workflows/templates', (route) => json(route, []));
  await page.route('**/api/v1/workflows/jobs?**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(pageEnvelope([])) }));
  await page.route('**/api/v1/workflows?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ ...pageEnvelope([]), summary: { total: 0, running: 0, waiting: 0, completed: 0 } }),
  }));

  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'archive-token', savedUser: user });
  await page.goto('/admin/infrastructure?tab=daily-archive');
}

test('legacy daily archive route resolves to the focused task and storage console', async ({ page }) => {
  await openArchiveConsole(page);
  await expect(page).toHaveURL(/\/data-console$/);
  await expect(page.getByRole('heading', { name: '数据管理台' })).toBeVisible();
  await expect(page.getByRole('tab', { name: /生产任务/ })).toBeVisible();
  await expect(page.getByRole('tab', { name: /文件存储/ })).toBeVisible();
  await expect(page.getByText('非破坏式每日整理')).toHaveCount(0);
});
