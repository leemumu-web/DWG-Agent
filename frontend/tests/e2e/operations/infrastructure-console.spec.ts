import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-20T00:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'infra-e2e', timestamp: now } });
const pageEnvelope = (data: unknown[]) => ({
  ...envelope(data),
  pagination: { page: 1, page_size: 20, total: data.length },
});
const user = { id: 1, username: 'admin', real_name: '系统管理员', status: 'active', roles: [{ id: 1, code: 'super_admin', name: '系统管理员', is_system: true }], created_at: now, updated_at: now };

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(envelope(data)) });
}

async function mockConsole(page: Page) {
  const dataOverview = { status: 'ok', environment: { app_env: 'production', database_engine: 'mysql', database: 'dwg_agent', storage_backend: 'minio' }, database: { status: 'ok' }, storage: { status: 'ok', areas: [{ bucket: 'factory-source', purpose_codes: ['source_dwg'] }, { bucket: 'factory-results', purpose_codes: ['derived_dwg', 'derived_dxf'] }], capacity: { status: 'warning', total_bytes: 1000, used_bytes: 820, free_bytes: 180, used_percent: 82, reason: null, checked_at: now } }, catalog: { available_files: 5, deleted_files: 0, tracked_bytes: 1024 }, transfers_today: { inbound_succeeded: 2, outbound_succeeded: 1, attention_required: 0 }, latest_scan: null };
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, { access_token: 'infra-token', user }, 201));
  await page.route('**/api/v1/data-admin/overview', (route) => json(route, dataOverview));
  await page.route('**/api/v1/workflows/templates', (route) => json(route, [{
    code: 'linux_production', name: '生产流程', description: '正式生产', stages: [],
  }]));
  await page.route('**/api/v1/workflows/jobs?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(pageEnvelope([])),
  }));
  await page.route('**/api/v1/workflows?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ ...pageEnvelope([]), summary: { total: 0, running: 0, waiting: 0, completed: 0 } }),
  }));
  await page.route('**/api/v1/data-admin/objects/tree?**', (route) => json(route, {
    bucket: 'factory-source',
    prefix: '',
    folders: [],
    objects: [],
    truncated: false,
  }));
}

async function mockConsoleWithFailedJob(page: Page) {
  await mockConsole(page);
  await page.unroute('**/api/v1/workflows/jobs?**');
  await page.route('**/api/v1/workflows/jobs?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(pageEnvelope([{
      id: 91,
      project_id: 7,
      drawing_id: null,
      created_by: 1,
      task_type: 'split_steel_dxf',
      precision_level: 'normal',
      pipeline: 'steel_dxf_split',
      status: 'failed',
      attempt: 1,
      priority: 0,
      progress: 63,
      params_json: { workflow_id: 31 },
      error_code: 'DXF_SPLIT_SOURCE_MISSING',
      error_message: 'Traceback: SQLAlchemy Exception in /app/private/worker.py',
      progress_data: null,
      created_at: now,
      updated_at: now,
      started_at: now,
      finished_at: now,
    }])) ,
  }));
}

test('legacy infrastructure route resolves to the focused task and storage console', async ({ page }) => {
  const requested = new Set<string>();
  page.on('request', (request) => {
    if (request.url().includes('/api/v1/')) requested.add(new URL(request.url()).pathname);
  });
  await mockConsole(page);
  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => { sessionStorage.setItem('dwg_access_token', token); sessionStorage.setItem('dwg_user', JSON.stringify(savedUser)); }, { token: 'infra-token', savedUser: user });
  await page.goto('/admin/infrastructure');
  await expect(page).toHaveURL(/\/data-console$/);
  await expect(page.getByRole('heading', { name: '数据管理台' })).toBeVisible();
  await expect(page.locator('.data-console-hero').getByText('业务数据库 正常')).toBeVisible();
  await expect(page.locator('.data-console-hero').getByText('文件存储 正常')).toBeVisible();
  await expect(page.locator('.data-console-hero').getByText('容量 82.0%')).toBeVisible();
  await expect(page.getByRole('tab', { name: /生产任务/ })).toBeVisible();
  await expect(page.getByRole('tab', { name: /文件存储/ })).toBeVisible();
  await expect(page.getByRole('tab', { name: '运行与通信' })).toHaveCount(0);
  const [treeResponse] = await Promise.all([
    page.waitForResponse((response) => response.url().includes('/api/v1/data-admin/objects/tree')),
    page.getByRole('tab', { name: /文件存储/ }).click(),
  ]);
  expect(treeResponse.status()).toBe(200);
  await expect(page.locator('.storage-tree-card').getByText('原始 DWG')).toBeVisible();
  await expect(page.getByText('转换后 DWG / 处理后 DXF')).toBeVisible();
  expect(requested).toEqual(new Set([
    '/api/v1/auth/tokens/refresh',
    '/api/v1/data-admin/overview',
    '/api/v1/workflows',
    '/api/v1/workflows/templates',
    '/api/v1/workflows/jobs',
    '/api/v1/data-admin/objects/tree',
  ]));
});

test('task errors show an operator action without backend logs or codes', async ({ page }) => {
  await mockConsoleWithFailedJob(page);
  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'infra-token', savedUser: user });
  await page.goto('/data-console');
  await page.getByRole('tab', { name: /处理任务/ }).click();
  await page.getByRole('button', { name: '查看原因' }).click();

  await expect(page.getByRole('dialog', { name: '任务处理说明' })).toBeVisible();
  await expect(page.getByText('分类后的拆板图纸已缺失，请返回图纸分类阶段核对并重新确认。')).toBeVisible();
  await expect(page.getByText(/进入所属生产项目，返回图纸分类阶段/)).toBeVisible();
  await expect(page.getByText(/Traceback|SQLAlchemy|Exception|\/app\/private|DXF_SPLIT_SOURCE_MISSING/)).toHaveCount(0);
});

test('storage console displays and downloads the registered original filename', async ({ page }) => {
  await mockConsole(page);
  await page.unroute('**/api/v1/data-admin/objects/tree?**');
  await page.route('**/api/v1/data-admin/objects/tree?**', (route) => json(route, {
    bucket: 'factory-source',
    prefix: 'uploads/',
    folders: [],
    objects: [{
      bucket: 'factory-source',
      storage_key: 'uploads/6fc5163347f84ca0a198b42402497168.dwg',
      original_name: '首体院-B7-钢梁原图.dwg',
      size_bytes: 2048,
      last_modified: now,
      registered: true,
      file_id: 501,
      file_status: 'available',
    }],
    truncated: false,
  }));
  await page.route('**/api/v1/files/501/download-url', (route) => json(route, {
    url: '/mock-download/501',
    expires_in: 300,
  }));
  await page.route('**/mock-download/501', (route) => route.fulfill({
    status: 200,
    contentType: 'application/acad',
    body: 'AC1027',
  }));
  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'infra-token', savedUser: user });
  await page.goto('/data-console?tab=storage');

  await expect(page.getByText('首体院-B7-钢梁原图.dwg')).toBeVisible();
  await expect(page.getByText('6fc5163347f84ca0a198b42402497168.dwg')).toHaveCount(0);
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('首体院-B7-钢梁原图.dwg');
});

test('API failures hide technical response details and retain the request number', async ({ page }) => {
  await mockConsole(page);
  await page.unroute('**/api/v1/data-admin/overview');
  await page.route('**/api/v1/data-admin/overview', (route) => route.fulfill({
    status: 500,
    contentType: 'application/json',
    body: JSON.stringify({
      error: {
        code: 'STORAGE_LIST_FAILED',
        message: 'Traceback: pymysql OperationalError at /app/backend.py',
      },
      meta: { request_id: 'worker-visible-request', timestamp: now },
    }),
  }));
  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'infra-token', savedUser: user });
  await page.goto('/data-console');

  await expect(page.getByText(/文件存储暂时无法读取/)).toBeVisible();
  await expect(page.getByText(/请求编号 worker-visible-request/)).toBeVisible();
  await expect(page.getByText(/Traceback|pymysql|OperationalError|\/app\/backend|STORAGE_LIST_FAILED/)).toHaveCount(0);
});
