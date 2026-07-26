import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  API_BASE,
} from '../support/test-env';

async function login(page: Page) {
  const response = await page.request.post(`${API_BASE}/api/v1/auth/sessions`, {
    data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD },
  });
  expect(response.status()).toBe(201);
  const body = await response.json();
  await page.goto('/');
  await page.evaluate(
    ({ token, user }) => {
      sessionStorage.setItem('dwg_access_token', token);
      sessionStorage.setItem('dwg_user', JSON.stringify(user));
    },
    { token: body.data.access_token, user: body.data.user },
  );
}

test('data console manages current production tasks and registered file storage', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await login(page);

  const [tasksResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/workflows?')),
    page.goto('/data-console'),
  ]);
  expect(tasksResponse.status()).toBe(200);
  await expect(page.getByRole('heading', { name: '数据管理台' })).toBeVisible();
  await expect(page.locator('.data-console-hero').getByText('业务数据库 正常')).toBeVisible();
  await expect(page.getByRole('tab', { name: /生产任务/ })).toBeVisible();
  await expect(page.getByRole('tab', { name: /文件存储/ })).toBeVisible();
  await expect(page.getByText('当前生产任务', { exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: '生产项目' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '处理任务' })).toBeVisible();

  const [treeResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/data-admin/objects/tree')),
    page.getByRole('tab', { name: /文件存储/ }).click(),
  ]);
  expect(treeResponse.status()).toBe(200);
  await expect(page.getByText('文件存储区')).toBeVisible();
  await expect(page.getByRole('button', { name: '刷新' }).first()).toBeVisible();

  await page.screenshot({
    path: path.resolve(process.cwd(), '../output/playwright/data-console-final.png'),
    fullPage: true,
  });
  expect(consoleErrors).toEqual([]);
});

test('workflow and audit pages request one bounded server page', async ({ page }) => {
  await login(page);
  const [workflowsResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/workflows?')),
    page.goto('/workflows'),
  ]);
  const workflowsUrl = new URL(workflowsResponse.url());
  expect(workflowsUrl.searchParams.get('page')).toBe('1');
  expect(workflowsUrl.searchParams.get('page_size')).toBe('20');

  const [auditResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/audit-logs?')),
    page.goto('/admin/audit-logs'),
  ]);
  const auditUrl = new URL(auditResponse.url());
  expect(auditUrl.searchParams.get('page')).toBe('1');
  expect(auditUrl.searchParams.get('page_size')).toBe('20');
});
