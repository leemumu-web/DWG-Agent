import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { expect, test, type Page } from '@playwright/test';

import { API_BASE } from '../support/test-env';

const VALID_SAMPLE = process.env.PLAYWRIGHT_EXCEL_SAMPLE_PATH;

async function login(page: Page): Promise<string> {
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
  await page.goto('/files/excel-final');
  return body.data.access_token as string;
}

function auth(token: string) {
  return { Authorization: `Bearer ${token}` };
}

async function waitForJob(page: Page, token: string, jobId: number, attempt: number) {
  let body: any;
  await expect.poll(
    async () => {
      const response = await page.request.get(`${API_BASE}/api/v1/jobs/${jobId}`, {
        headers: auth(token),
      });
      body = await response.json();
      return `${body.data.attempt}:${body.data.status}`;
    },
    { timeout: 45_000, intervals: [250, 500, 1000] },
  ).toMatch(new RegExp(`^${attempt}:(succeeded|failed|cancelled)$`));
  return body.data;
}

test.describe('Excel Final retry and download closure', () => {
  test('real XLS upload succeeds and download retry obtains a fresh signature', async ({ page }) => {
    test.skip(!VALID_SAMPLE || !fs.existsSync(VALID_SAMPLE), 'Set PLAYWRIGHT_EXCEL_SAMPLE_PATH');
    const token = await login(page);

    await page.locator('.ant-upload input[type="file"]').setInputFiles(path.resolve(VALID_SAMPLE!));
    const [submitResponse] = await Promise.all([
      page.waitForResponse(
        (response) => response.url().includes('/api/v1/excel-final/upload-and-process')
          && response.request().method() === 'POST',
      ),
      page.getByRole('button', { name: '提交处理' }).click(),
    ]);
    expect(submitResponse.status()).toBe(202);
    const submitted = await submitResponse.json();
    const jobId = submitted.data.job_id as number;

    const job = await waitForJob(page, token, jobId, 1);
    expect(job.status).toBe('succeeded');

    let signedUrlRequests = 0;
    let downloadAttempts = 0;
    page.on('request', (request) => {
      if (request.url().includes('/download-url')) signedUrlRequests += 1;
    });
    await page.route(/\/api\/v1\/files\/\d+\/download\?/, async (route) => {
      downloadAttempts += 1;
      if (downloadAttempts === 1) {
        await route.fulfill({
          status: 403,
          contentType: 'application/json',
          headers: {
            'access-control-allow-credentials': 'true',
            'access-control-allow-origin': 'http://127.0.0.1:5174',
          },
          body: JSON.stringify({
            error: { code: 'DOWNLOAD_URL_EXPIRED', message: 'Download URL has expired.' },
          }),
        });
        return;
      }
      await route.continue();
    });

    const downloadButton = page.getByRole('button', { name: '下载结果' }).first();
    await expect(downloadButton).toBeVisible({ timeout: 10_000 });
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 20_000 }),
      downloadButton.click(),
    ]);
    const downloadedPath = await download.path();
    expect(downloadedPath).not.toBeNull();

    const statusResponse = await page.request.get(
      `${API_BASE}/api/v1/excel-final/process/${jobId}`,
      { headers: auth(token) },
    );
    const resultFileId = (await statusResponse.json()).data.result_file_id as number;
    const fileResponse = await page.request.get(`${API_BASE}/api/v1/files/${resultFileId}`, {
      headers: auth(token),
    });
    const expectedHash = (await fileResponse.json()).data.sha256 as string;
    const actualHash = createHash('sha256').update(fs.readFileSync(downloadedPath!)).digest('hex');

    expect(actualHash).toBe(expectedHash);
    expect(downloadAttempts).toBe(2);
    expect(signedUrlRequests).toBe(2);
  });

  test('invalid XLS shows safe failure and retry advances the attempt', async ({ page }) => {
    const token = await login(page);
    await page.locator('.ant-upload input[type="file"]').setInputFiles({
      name: 'invalid-e2e.xls',
      mimeType: 'application/vnd.ms-excel',
      buffer: Buffer.from('not a supported spreadsheet'),
    });
    const [submitResponse] = await Promise.all([
      page.waitForResponse(
        (response) => response.url().includes('/api/v1/excel-final/upload-and-process')
          && response.request().method() === 'POST',
      ),
      page.getByRole('button', { name: '提交处理' }).click(),
    ]);
    expect(submitResponse.request().headers()['idempotency-key']).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    const jobId = (await submitResponse.json()).data.job_id as number;

    const first = await waitForJob(page, token, jobId, 1);
    expect(first.status).toBe('failed');
    expect(first.error_message).toContain('流水线处理失败');
    expect(first.error_message.length).toBeLessThan(300);
    expect(first.error_message).not.toMatch(/Traceback|\/home\/|pandas\.errors/);

    const row = page.locator(`.ant-table-row[data-row-key="${jobId}"]`);
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect(row).toContainText('失败', { timeout: 10_000 });
    const [retryResponse] = await Promise.all([
      page.waitForResponse(
        (response) => response.url().includes(`/api/v1/jobs/${jobId}/retry-requests`)
          && response.request().method() === 'POST',
      ),
      row.getByTitle('重新提交').click(),
    ]);
    expect(retryResponse.status()).toBe(202);
    expect((await retryResponse.json()).data.attempt).toBe(2);

    const second = await waitForJob(page, token, jobId, 2);
    expect(second.status).toBe('failed');
    expect(second.error_message).not.toMatch(/Traceback|\/home\/|pandas\.errors/);
    const stepsResponse = await page.request.get(
      `${API_BASE}/api/v1/jobs/${jobId}/steps?attempt=2&page_size=200`,
      { headers: auth(token) },
    );
    const steps = (await stepsResponse.json()).data;
    expect(steps.map((step: any) => [step.attempt, step.step_name, step.status])).toEqual([
      [2, 'download_excel_source', 'succeeded'],
      [2, 'run_excel_final_pipeline', 'failed'],
    ]);
  });
});

test('Excel Final data console exposes exact overview, tools, details and URL job tracking', async ({ page }) => {
  const consoleErrors: string[] = [];
  const batchQueries: string[] = [];
  const searchQueries: string[] = [];
  page.on('console', (entry) => {
    if (entry.type() === 'error') consoleErrors.push(entry.text());
  });
  const envelope = (data: unknown) => ({ data, meta: { request_id: 'excel-console-e2e' } });
  const paged = (data: unknown[], pageNo = 1, pageSize = 20, total = data.length) => ({
    ...envelope(data),
    pagination: { page: pageNo, page_size: pageSize, total, total_pages: Math.ceil(total / pageSize) },
  });
  await page.route('**/api/v1/jobs?**', (route) => route.fulfill({ json: paged([]) }));
  await page.route('**/api/v1/files/901/excel-preview**', (route) => route.fulfill({
    json: envelope({
      file_id: 901,
      file_name: 'final-777.xlsx',
      sheet: '整理表',
      sheets: ['整理表'],
      headers: ['零件号', '材质'],
      rows: [{ 零件号: 'P-900', 材质: 'Q355' }],
      total_rows: 1,
      preview_rows: 1,
    }),
  }));
  await page.route('**/api/v1/excel-final/**', async (route) => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (pathname.endsWith('/health')) {
      await route.fulfill({ json: envelope({
        pipeline_enabled: true,
        stage_available: true,
        dependencies_available: true,
        package_available: true,
        handbook_available: true,
        handbook_database_available: true,
        database_backend: 'sqlite',
        database_available: true,
        storage_backend: 'local',
        storage_available: true,
        storage_bucket: 'dwg-reports',
        degraded_components: [],
        ready: true,
      }) });
    } else if (pathname.endsWith('/overview')) {
      await route.fulfill({ json: envelope({
        batch_count: 42,
        part_count: 12840,
        component_count: 936,
        total_net_weight: 204850.75,
        total_gross_weight: 218100.5,
        latest_created_at: '2026-07-13T01:20:00Z',
      }) });
    } else if (pathname.endsWith('/process/777')) {
      await route.fulfill({ json: envelope({
        job_id: 777,
        status: 'succeeded',
        progress: 100,
        pipeline: 'excel_final',
        error_code: null,
        error_message: null,
        created_at: '2026-07-13T01:00:00Z',
        started_at: '2026-07-13T01:00:01Z',
        finished_at: '2026-07-13T01:00:03Z',
        batch: null,
        result_file_id: 901,
      }) });
    } else if (pathname.endsWith('/parts/search')) {
      searchQueries.push(url.search);
      await route.fulfill({ json: paged([{
        id: 900,
        batch_id: 41,
        seq: 1,
        component_no: 'GZ-01',
        part_type: '零件',
        part_no: 'P-900',
        spec: 'PL12*280',
        width: 280,
        length: 1200,
        material: 'Q355',
        qty: 4,
        net_total_weight: 126.4,
        theo_total_weight: 130.1,
      }], Number(url.searchParams.get('page') || 1), Number(url.searchParams.get('page_size') || 20), 41) });
    } else if (pathname.endsWith('/weights/lookup')) {
      await route.fulfill({ json: envelope({
        spec: url.searchParams.get('spec'),
        weight_kg_per_m: 3.77,
        source: 'hardware_handbook',
      }) });
    } else if (pathname.endsWith('/batches/41/parts/900')) {
      await route.fulfill({ json: envelope({
        id: 900, batch_id: 41, seq: 1, component_no: 'GZ-01', component_qty: 2,
        part_type: '零件', part_no: 'P-900', profile_spec: 'PL12', spec: 'PL12*280',
        width: 280, length: 1200, left_inset: 0, right_inset: 0, cut_length: 1200,
        material: 'Q355', qty: 4, total_qty: 8, total_length: 9600, density: 7850,
        theo_unit_weight: 32.5, theo_total_weight: 130, net_unit_weight: 31.6,
        net_total_weight: 126.4, table_net_weight: 126.4, gross_unit_weight: 34,
        gross_total_weight: 136, table_gross_weight: 136, surface_area: 0.8,
        total_surface_area: 6.4, created_at: '2026-07-13T01:20:00Z',
      }) });
    } else if (pathname.endsWith('/batches/41/parts')) {
      await route.fulfill({ json: paged([{
        id: 900, seq: 1, component_no: 'GZ-01', component_qty: 2, part_type: '零件',
        part_no: 'P-900', profile_spec: 'PL12', spec: 'PL12*280', width: 280,
        length: 1200, cut_length: 1200, material: 'Q355', qty: 4, total_qty: 8,
        total_length: 9600, density: 7850, theo_unit_weight: 32.5,
        theo_total_weight: 130, net_unit_weight: 31.6, net_total_weight: 126.4,
        table_net_weight: 126.4, gross_unit_weight: 34, gross_total_weight: 136,
        table_gross_weight: 136, surface_area: 0.8, total_surface_area: 6.4,
      }], 1, 50, 1) });
    } else if (pathname.endsWith('/batches/41/components')) {
      await route.fulfill({ json: paged([
        { id: 501, component_no: 'GZ-01', component_qty: 2, total_weight: 252.8 },
      ], Number(url.searchParams.get('page') || 1), Number(url.searchParams.get('page_size') || 20), 61) });
    } else if (pathname.endsWith('/batches/41')) {
      await route.fulfill({ json: envelope({
        batch_id: 41, job_id: 777, file_id: 901, source_type: 'init_table',
        source_name: 'tower-zone-a.xlsx', part_count: 320, component_count: 61,
        total_net_weight: 4850.5, total_gross_weight: 5110.2,
        created_at: '2026-07-13T01:20:00Z',
        material_breakdown: [{ material: 'Q355', count: 300, total_net_weight: 4700 }],
        top_specs: [{ spec: 'PL12*280', count: 75 }],
      }) });
    } else if (pathname.endsWith('/batches')) {
      batchQueries.push(url.search);
      await route.fulfill({ json: paged([{
        batch_id: 41, job_id: 777, file_id: 901, source_type: 'init_table',
        source_name: 'tower-zone-a.xlsx', part_count: 320, component_count: 61,
        total_net_weight: 4850.5, total_gross_weight: 5110.2,
        created_at: '2026-07-13T01:20:00Z',
      }], Number(url.searchParams.get('page') || 1), Number(url.searchParams.get('page_size') || 20), 141) });
    } else {
      await route.fallback();
    }
  });

  await login(page);
  await page.goto(
    '/files/excel-final?job_id=777&batch_page=3&batch_size=50&batch_id=41'
      + '&part_no=P-900&search_page=2&search_size=20',
  );

  await expect(page.getByRole('heading', { name: 'Excel Final 数据控制台' })).toBeVisible();
  await expect(page.getByText('12,840')).toBeVisible();
  await expect(page.getByText('204,850.75')).toBeVisible();
  await expect(page.getByText('数据管道就绪')).toBeVisible();
  await expect(page.getByText('SQLite 权威数据')).toBeVisible();
  await expect(page.getByText('本地对象存储')).toBeVisible();
  await expect(page.getByText(/MinIO/)).toHaveCount(0);
  const heroDescription = page.getByText('监视处理任务、核对业务数据库入库记录，并预览对象存储中的最终清单。');
  await expect(heroDescription).toHaveCSS('color', 'rgb(185, 206, 216)');
  await expect(page.getByText(/最近刷新/)).toBeVisible();
  await expect(page.getByText('任务 #777')).toBeVisible();
  await expect(page.getByLabel('跨批次零件号')).toHaveValue('P-900');
  await expect(page.getByText('PL12*280').first()).toBeVisible();
  await expect.poll(() => batchQueries.some((query) => query.includes('page=3') && query.includes('page_size=50'))).toBe(true);
  await expect.poll(() => searchQueries.some((query) => query.includes('page=2') && query.includes('part_no=P-900'))).toBe(true);

  const drawer = page.getByRole('dialog', { name: /批次 #41/ });
  await expect(drawer.getByText('tower-zone-a.xlsx')).toBeVisible();
  await drawer.getByRole('tab', { name: /构件/ }).click();
  await expect(drawer.getByRole('tabpanel', { name: /构件/ }).getByText('GZ-01')).toBeVisible();
  await drawer.getByRole('tab', { name: /零件/ }).click();
  await drawer.getByRole('button', { name: '查看零件 P-900' }).click();
  const partDialog = page.getByRole('dialog', { name: /零件 P-900/ });
  await expect(partDialog).toBeVisible();
  await partDialog.getByRole('button', { name: 'Close' }).click();
  await expect(partDialog).toBeHidden();
  await drawer.getByRole('button', { name: '关闭' }).click();
  await expect(drawer).toBeHidden();
  expect(new URL(page.url()).searchParams.has('batch_id')).toBe(false);
  expect(new URL(page.url()).searchParams.get('job_id')).toBe('777');
  await page.goBack();
  await expect(drawer).toBeVisible();
  await drawer.getByRole('button', { name: '关闭' }).click();

  await page.getByRole('button', { name: '清空搜索' }).click();
  await expect(page.getByText('P-900')).toBeHidden();
  const clearedUrl = new URL(page.url());
  expect(clearedUrl.searchParams.has('part_no')).toBe(false);
  expect(clearedUrl.searchParams.has('search_page')).toBe(false);
  expect(clearedUrl.searchParams.get('job_id')).toBe('777');

  await page.getByLabel('钢材规格').fill('L50x5');
  await page.getByRole('button', { name: '查询理论重量' }).click();
  await expect(page.getByText('3.77 kg/m')).toBeVisible();

  await page.getByRole('button', { name: '预览任务 777 结果' }).click();
  await expect(page.getByRole('dialog', { name: /excel-final-777\.xlsx/ })).toBeVisible();
  await expect(page.getByText('Q355').last()).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('DXF to Excel result can be registered once as an Excel Final job', async ({ page }) => {
  const envelope = (data: unknown) => ({ data, meta: { request_id: 'bridge-e2e' } });
  const paged = (data: unknown[]) => ({
    ...envelope(data),
    pagination: { page: 1, page_size: 200, total: data.length, total_pages: 1 },
  });
  let processCalls = 0;
  let processRequestKey: string | undefined;
  await page.route('**/api/v1/files/batches?**', (route) => route.fulfill({ json: envelope([{
    name: 'bridge-batch', file_count: 3, total_size: 4096,
    latest_created_at: '2026-07-13T02:00:00Z',
  }]) }));
  await page.route('**/api/v1/jobs?**', (route) => route.fulfill({ json: paged([{
    id: 700, task_type: 'extract_dxf_to_excel', status: 'succeeded', progress: 100,
    pipeline: 'dxf2excel', params_json: { batch_name: 'bridge-batch' },
    created_at: '2026-07-13T02:00:00Z', updated_at: '2026-07-13T02:00:02Z',
  }]) }));
  await page.route('**/api/v1/jobs/700/results**', (route) => route.fulfill({ json: paged([{
    id: 701, job_id: 700, result_type: 'extract_dxf_to_excel', result_file_id: 880,
    summary_json: null, metrics_json: null, created_at: '2026-07-13T02:00:02Z',
  }]) }));
  await page.route('**/api/v1/excel-final/process?**', async (route) => {
    processCalls += 1;
    processRequestKey = route.request().headers()['idempotency-key'];
    await new Promise((resolve) => setTimeout(resolve, 250));
    await route.fulfill({ status: 202, json: envelope({
      job_id: 990, file_id: 880, status: 'queued', reused: false,
      message: '处理任务已入队',
    }) });
  });

  await login(page);
  await page.goto('/files/dxf2excel');
  const bridgeButton = page.getByRole('button', { name: '生成零件清单' });
  await expect(bridgeButton).toBeVisible();
  await bridgeButton.click();
  const confirm = page.getByRole('button', { name: '确认生成' });
  await confirm.dblclick();

  await expect(page).toHaveURL(/\/files\/excel-final\?job_id=990$/);
  expect(processCalls).toBe(1);
  expect(processRequestKey).toBe('dxf2excel-700-880');
  await expect(page.getByRole('heading', { name: 'Excel Final 数据控制台' })).toBeVisible();
});
