import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { API_BASE } from '../support/test-env';

async function login(page: Page) {
  const response = await page.request.post(`${API_BASE}/api/v1/auth/sessions`, {
    data: { username: 'admin', password: 'SuperAdminPass1' },
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

test('data console monitors paged MySQL, storage, transfers and findings', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await login(page);

  const [overviewResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/data-admin/overview')),
    page.goto('/admin/infrastructure?tab=overview'),
  ]);
  expect(overviewResponse.status()).toBe(200);
  const overviewBody = await overviewResponse.json();
  const latestScanId = overviewBody.data?.latest_scan?.id as number | undefined;
  await expect(page.getByRole('heading', { name: '数据控制台' })).toBeVisible();
  await expect(page.locator('.data-console-hero').getByText('正常', { exact: true })).toBeVisible();
  for (const tab of ['总览', '文件登记', '存储对象', '流转流水', '一致性']) {
    await expect(page.getByRole('tab', { name: tab })).toBeVisible();
  }

  const [filesResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/data-admin/files?')),
    page.getByRole('tab', { name: '文件登记' }).click(),
  ]);
  const filesUrl = new URL(filesResponse.url());
  expect(filesUrl.searchParams.get('page')).toBe('1');
  expect(filesUrl.searchParams.get('page_size')).toBe('20');
  await expect(page.getByText('MySQL 文件登记')).toBeVisible();
  const viewFile = page.getByRole('button', { name: '查看' }).first();
  if (await viewFile.count()) {
    await viewFile.click();
    await expect(page.getByText('登记详情')).toBeVisible();
    await page.locator('.ant-drawer-close').click();
  }

  const [objectsResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/data-admin/objects?')),
    page.getByRole('tab', { name: '存储对象' }).click(),
  ]);
  const objectsUrl = new URL(objectsResponse.url());
  expect(objectsUrl.searchParams.get('bucket')).toBe('dwg-original');
  expect(objectsUrl.searchParams.get('page_size')).toBe('50');
  await expect(page.getByText('对象存储清单')).toBeVisible();

  const [transfersResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/data-admin/transfers?')),
    page.getByRole('tab', { name: '流转流水' }).click(),
  ]);
  const transfersUrl = new URL(transfersResponse.url());
  expect(transfersUrl.searchParams.get('page')).toBe('1');
  expect(transfersUrl.searchParams.get('page_size')).toBe('20');
  await expect(page.getByText('入库 / 出库流水')).toBeVisible();
  await page.getByRole('combobox', { name: '流水状态筛选' }).click();
  await expect(page.getByText('待补偿', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  const viewTransfer = page.getByRole('button', { name: '查看' }).first();
  if (await viewTransfer.count()) {
    await viewTransfer.click();
    const drawer = page.locator('.ant-drawer');
    await expect(drawer.getByText('流水详情')).toBeVisible();
    await expect(drawer).not.toContainText(/inbound \/|outbound \/|internal \/|\/ upload|\/ download/);
    await page.locator('.ant-drawer-close').click();
  }

  if (latestScanId) {
    const findingsPromise = page.waitForResponse(
      (response) => response.url().includes(`/scans/${latestScanId}/findings?`)
        && response.url().includes('resolution_status=open'),
    );
    await page.getByRole('tab', { name: '一致性' }).click();
    const findingsResponse = await findingsPromise;
    expect(findingsResponse.status()).toBe(200);
  } else {
    const invalidFindingsRequests: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/findings?')) invalidFindingsRequests.push(request.url());
    });
    await page.getByRole('tab', { name: '一致性' }).click();
    await expect(page.getByText('选择“开始扫描”生成 MySQL 与对象存储的时间点快照。')).toBeVisible();
    expect(invalidFindingsRequests).toEqual([]);
  }
  await expect(page.getByText('异常明细')).toBeVisible();
  await expect(page.getByLabel('异常类型筛选')).toBeVisible();
  await expect(page.getByLabel('处置状态筛选')).toBeVisible();
  await expect(page.getByLabel('处置动作')).toBeDisabled();
  await expect(page.getByText('先选择同类异常', { exact: true })).toBeVisible();

  await page.screenshot({
    path: path.resolve(process.cwd(), '../output/playwright/data-console-final.png'),
    fullPage: true,
  });
  expect(consoleErrors).toEqual([]);
});

test('jobs and audit pages request one bounded server page', async ({ page }) => {
  await login(page);
  const [jobsResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/jobs?')),
    page.goto('/jobs'),
  ]);
  const jobsUrl = new URL(jobsResponse.url());
  expect(jobsUrl.searchParams.get('page')).toBe('1');
  expect(jobsUrl.searchParams.get('page_size')).toBe('20');

  const [auditResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/audit-logs?')),
    page.goto('/admin/audit-logs'),
  ]);
  const auditUrl = new URL(auditResponse.url());
  expect(auditUrl.searchParams.get('page')).toBe('1');
  expect(auditUrl.searchParams.get('page_size')).toBe('20');
});
