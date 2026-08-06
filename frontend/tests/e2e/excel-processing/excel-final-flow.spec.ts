import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { expect, test, type Page } from '@playwright/test';

import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  API_BASE,
  EXCEL_FINAL_PIPELINE_ENABLED,
} from '../support/test-env';

const VALID_SAMPLE = process.env.PLAYWRIGHT_EXCEL_SAMPLE_PATH;

async function login(page: Page): Promise<string> {
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
      const response = await page.request.get(`${API_BASE}/api/v1/workflows/jobs/${jobId}`, {
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
  test('work center tabs defer heavy queries and submit an explicit handbook key', async ({ page }) => {
    const envelope = (data: unknown) => ({
      data,
      meta: { request_id: 'excel-tabs-e2e' },
    });
    const paged = (data: unknown[]) => ({
      ...envelope(data),
      pagination: { page: 1, page_size: 20, total: data.length, total_pages: 1 },
    });
    let batchCalls = 0;
    let searchCalls = 0;
    let handbookCalls = 0;
    let handbookQuery = '';
    await page.route('**/api/v1/workflows/jobs?**', (route) => route.fulfill({ json: paged([]) }));
    await page.route('**/api/v1/excel-final/**', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/health')) {
        await route.fulfill({ json: envelope({
          pipeline_enabled: true,
          stage_available: true,
          dependencies_available: true,
          package_available: true,
          handbook_available: true,
          handbook_database_available: true,
          database_backend: 'mysql',
          database_available: true,
          storage_backend: 'local',
          storage_available: true,
          storage_bucket: 'dwg-reports',
          max_upload_size_bytes: 512 * 1024 * 1024,
          degraded_components: [],
          ready: true,
        }) });
      } else if (url.pathname.endsWith('/overview')) {
        await route.fulfill({ json: envelope({
          batch_count: 1,
          part_count: 2,
          component_count: 1,
          total_net_weight: 3,
          total_gross_weight: 4,
          latest_created_at: '2026-07-24T08:00:00Z',
        }) });
      } else if (url.pathname.endsWith('/batches')) {
        batchCalls += 1;
        await route.fulfill({ json: paged([]) });
      } else if (url.pathname.endsWith('/parts/search')) {
        searchCalls += 1;
        await route.fulfill({ json: paged([]) });
      } else if (url.pathname.endsWith('/weights/lookup')) {
        handbookCalls += 1;
        handbookQuery = url.search;
        await route.fulfill({ json: envelope({
          category: 'round_bar',
          spec: 'D8',
          normalized_spec: '8',
          material: 'Q235B',
          weight_kg_per_m: 0.395,
          source: 'round_square_bar:round_bar',
          status: 'hit',
        }) });
      } else {
        await route.fallback();
      }
    });

    await login(page);
    await expect(page.getByRole('heading', { name: 'Excel 第一阶段处理' })).toBeVisible();
    await expect(page.getByRole('tab', { name: '处理' })).toHaveAttribute('aria-selected', 'true');
    expect(batchCalls).toBe(0);
    expect(searchCalls).toBe(0);
    expect(handbookCalls).toBe(0);

    await page.getByRole('tab', { name: '批次', exact: true }).click();
    await expect.poll(() => batchCalls).toBe(1);
    expect(new URL(page.url()).searchParams.get('tab')).toBe('batches');

    await page.getByRole('tab', { name: '零件', exact: true }).click();
    await page.getByLabel('跨批次零件号').fill('P-1');
    await page.getByRole('button', { name: '搜索零件' }).click();
    await expect.poll(() => searchCalls).toBe(1);

    await page.getByRole('tab', { name: '五金手册', exact: true }).click();
    await page.getByRole('combobox', { name: '五金手册类别' }).click();
    await page.getByText('圆钢', { exact: true }).click();
    await page.getByLabel('钢材规格').fill('D8');
    await page.getByLabel('钢材材质').fill('Q235B');
    await page.getByRole('button', { name: '查询理论重量' }).click();
    await expect(page.getByText('0.395 kg/m')).toBeVisible();
    expect(handbookCalls).toBe(1);
    expect(new URLSearchParams(handbookQuery)).toEqual(new URLSearchParams({
      category: 'round_bar',
      spec: 'D8',
      material: 'Q235B',
    }));
  });

  test('Excel submit falls back when randomUUID is unavailable', async ({ page }) => {
    const envelope = (data: unknown) => ({
      data,
      meta: { request_id: 'excel-request-key-fallback' },
    });
    const paged = (data: unknown[]) => ({
      ...envelope(data),
      pagination: { page: 1, page_size: 50, total: data.length, total_pages: 1 },
    });
    let requestKey = '';

    await page.addInitScript(() => {
      Object.defineProperty(globalThis.crypto, 'randomUUID', {
        configurable: true,
        value: undefined,
      });
    });
    await page.route('**/api/v1/auth/tokens/refresh', (route) => route.fulfill({
      json: envelope({
        access_token: 'excel-request-key-token',
        token_type: 'bearer',
        user: {
          id: 1,
          username: 'admin',
          real_name: 'Admin',
          status: 'active',
          password_reset_required: false,
          roles: [{ id: 1, code: 'super_admin', name: 'Super Admin', is_system: true }],
          created_at: '2026-08-06T00:00:00Z',
          updated_at: '2026-08-06T00:00:00Z',
        },
      }),
    }));
    await page.route('**/api/v1/workflows/jobs?**', (route) => route.fulfill({ json: paged([]) }));
    await page.route('**/api/v1/excel-final/**', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/health')) {
        await route.fulfill({ json: envelope({
          pipeline_enabled: true,
          stage_available: true,
          dependencies_available: true,
          package_available: true,
          handbook_available: true,
          handbook_database_available: true,
          database_backend: 'mysql',
          database_available: true,
          storage_backend: 'local',
          storage_available: true,
          storage_bucket: 'dwg-reports',
          max_upload_size_bytes: 512 * 1024 * 1024,
          degraded_components: [],
          ready: true,
        }) });
      } else if (url.pathname.endsWith('/overview')) {
        await route.fulfill({ json: envelope({
          batch_count: 0,
          part_count: 0,
          component_count: 0,
          total_net_weight: 0,
          total_gross_weight: 0,
          latest_created_at: null,
        }) });
      } else if (url.pathname.endsWith('/upload-and-process')) {
        requestKey = route.request().headers()['idempotency-key'] ?? '';
        await route.fulfill({
          status: 202,
          json: envelope({
            job_id: 9101,
            file_id: 8101,
            original_name: 'fallback.xlsx',
            status: 'queued',
            reused: false,
            message: 'queued',
          }),
        });
      } else if (url.pathname.endsWith('/process/9101')) {
        await route.fulfill({ json: envelope({
          job_id: 9101,
          status: 'queued',
          progress: 0,
          result_file_id: null,
        }) });
      } else {
        await route.fallback();
      }
    });

    await page.goto('/files/excel-final');
    await page.locator('.ant-upload input[type="file"]').setInputFiles({
      name: 'fallback.xlsx',
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      buffer: Buffer.from('mock workbook'),
    });
    const submit = page.getByRole('button', { name: '\u63d0\u4ea4\u5904\u7406' });
    await expect(submit).toBeEnabled();
    await submit.click();

    await expect.poll(() => requestKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  test('invalid workbook response shows bounded operator guidance', async ({ page }) => {
    const envelope = (data: unknown) => ({
      data,
      meta: { request_id: 'excel-input-guidance' },
    });
    const paged = (data: unknown[]) => ({
      ...envelope(data),
      pagination: { page: 1, page_size: 50, total: data.length, total_pages: 1 },
    });
    await page.route('**/api/v1/workflows/jobs?**', (route) => route.fulfill({ json: paged([]) }));
    await page.route('**/api/v1/excel-final/health', (route) => route.fulfill({
      json: envelope({
        pipeline_enabled: true,
        stage_available: true,
        dependencies_available: true,
        package_available: true,
        handbook_available: true,
        handbook_database_available: true,
        database_backend: 'mysql',
        database_available: true,
        storage_backend: 'local',
        storage_available: true,
        storage_bucket: 'dwg-reports',
        max_upload_size_bytes: 512 * 1024 * 1024,
        degraded_components: [],
        ready: true,
      }),
    }));
    await page.route('**/api/v1/excel-final/overview', (route) => route.fulfill({
      json: envelope({
        batch_count: 0,
        part_count: 0,
        component_count: 0,
        total_net_weight: 0,
        total_gross_weight: 0,
        latest_created_at: null,
      }),
    }));
    await page.route('**/api/v1/excel-final/batches?**', (route) => route.fulfill({
      json: paged([]),
    }));
    await page.route('**/api/v1/excel-final/upload-and-process', (route) => route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'EXCEL_INPUT_REQUIRED_COLUMNS_MISSING',
          message: '表格缺少 Excel 第一阶段所需列。',
          details: {
            failure: {
              code: 'EXCEL_INPUT_REQUIRED_COLUMNS_MISSING',
              message: '表格缺少 Excel 第一阶段所需列。',
              action: '请在正式标题行中补充：数量。',
              contract_version: 1,
              issues: [{
                sheet: '原表',
                row: 6,
                column: null,
                field: '数量',
                value: null,
                reason: 'required_column_missing',
              }],
              sheets: ['原表'],
              meta: { issue_count: 1, server_path: '/home/private/input.xlsx' },
              traceback: 'Traceback: private internals',
            },
          },
        },
        meta: { request_id: 'excel-input-guidance' },
      }),
    }));

    await login(page);
    await page.locator('.ant-upload input[type="file"]').setInputFiles({
      name: 'missing-quantity.xlsx',
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      buffer: Buffer.from('mock workbook'),
    });
    await page.getByRole('button', { name: '提交处理' }).click();

    const failure = page.getByRole('alert', { name: '表格输入不符合规范' });
    await expect(failure).toBeVisible();
    await expect(failure).toContainText('表格缺少 Excel 第一阶段所需列。');
    await expect(failure).toContainText('请在正式标题行中补充：数量。');
    await expect(failure).toContainText('原表 · 第 6 行 · 数量');
    await expect(failure).toContainText('请求 excel-input-guidance');
    await expect(page.getByText(/\/home\/private|Traceback/)).toHaveCount(0);
  });

  test('oversized Excel is rejected before upload with the actual server limit', async ({ page }) => {
    const envelope = (data: unknown) => ({ data, meta: { request_id: 'excel-size-preflight' } });
    const paged = (data: unknown[]) => ({
      ...envelope(data),
      pagination: { page: 1, page_size: 50, total: data.length, total_pages: 1 },
    });
    let uploadRequests = 0;
    await page.route('**/api/v1/workflows/jobs?**', (route) => route.fulfill({ json: paged([]) }));
    await page.route('**/api/v1/excel-final/health', (route) => route.fulfill({
      json: envelope({
        pipeline_enabled: true,
        stage_available: true,
        dependencies_available: true,
        package_available: true,
        handbook_available: true,
        handbook_database_available: true,
        database_backend: 'mysql',
        database_available: true,
        storage_backend: 'local',
        storage_available: true,
        storage_bucket: 'dwg-reports',
        max_upload_size_bytes: 16,
        degraded_components: [],
        ready: true,
      }),
    }));
    await page.route('**/api/v1/excel-final/overview', (route) => route.fulfill({
      json: envelope({
        batch_count: 0,
        part_count: 0,
        component_count: 0,
        total_net_weight: 0,
        total_gross_weight: 0,
        latest_created_at: null,
      }),
    }));
    await page.route('**/api/v1/excel-final/upload-and-process', (route) => {
      uploadRequests += 1;
      return route.fulfill({ status: 500 });
    });

    await login(page);
    await page.locator('.ant-upload input[type="file"]').setInputFiles({
      name: 'unexpected-large.xlsx',
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      buffer: Buffer.alloc(17),
    });

    await expect(page.getByText('所选 Excel 为 17 B，超过服务器允许的 16 B。')).toBeVisible();
    await expect(page.getByRole('button', { name: '提交处理' })).toBeDisabled();
    expect(uploadRequests).toBe(0);
  });

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

  test('corrupt XLS is rejected before job creation with safe guidance', async ({ page }) => {
    test.skip(!EXCEL_FINAL_PIPELINE_ENABLED, 'Excel Final pipeline is disabled in this deployment');
    const token = await login(page);
    const jobsBeforeResponse = await page.request.get(
      `${API_BASE}/api/v1/workflows/jobs?task_type=excel_final&page_size=1`,
      { headers: auth(token) },
    );
    expect(jobsBeforeResponse.status()).toBe(200);
    const jobsBefore = (await jobsBeforeResponse.json()).pagination.total as number;

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
    expect(submitResponse.status()).toBe(422);
    const failureBody = await submitResponse.json();
    expect(failureBody.error.code).toBe('EXCEL_INPUT_TEXT_UNRECOGNIZED');
    expect(failureBody.error.details.failure.action).toContain('从 Tekla 重新导出');
    expect(JSON.stringify(failureBody)).not.toMatch(/Traceback|\/home\/|pandas\.errors/);

    const failure = page.getByRole('alert', { name: '表格输入不符合规范' });
    await expect(failure).toBeVisible();
    await expect(failure).toContainText('无法识别 Tekla 文本格式的 XLS 文件');
    await expect(failure).toContainText('从 Tekla 重新导出');

    const jobsAfterResponse = await page.request.get(
      `${API_BASE}/api/v1/workflows/jobs?task_type=excel_final&page_size=1`,
      { headers: auth(token) },
    );
    expect(jobsAfterResponse.status()).toBe(200);
    expect((await jobsAfterResponse.json()).pagination.total).toBe(jobsBefore);
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
  await page.route('**/api/v1/workflows/jobs?**', (route) => route.fulfill({ json: paged([]) }));
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
        category: 'angle',
        spec: url.searchParams.get('spec'),
        normalized_spec: 'L50x5',
        material: null,
        weight_kg_per_m: 3.77,
        source: 'hardware_handbook',
        status: 'hit',
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

  await expect(page.getByRole('heading', { name: 'Excel 第一阶段处理' })).toBeVisible();
  await expect(page.getByText('12,840')).toBeVisible();
  await expect(page.getByText('204,850.75')).toBeVisible();
  await expect(page.getByText('数据管道就绪')).toBeVisible();
  await expect(page.getByText('业务数据库')).toBeVisible();
  await expect(page.getByText('文件存储')).toBeVisible();
  await expect(page.getByText(/MinIO/)).toHaveCount(0);
  const heroDescription = page.getByText('校验原始 Tekla 清单，生成规范整理表和 part 表，并保留可追溯的计算痕迹。');
  await expect(heroDescription).toHaveCSS('color', 'rgb(185, 206, 216)');
  await expect(page.getByText(/最近刷新/)).toBeVisible();
  await expect(page.getByText('任务 #777')).toBeVisible();
  expect(batchQueries).toEqual([]);
  expect(searchQueries).toEqual([]);

  await page.getByRole('tab', { name: '批次', exact: true }).click();
  await expect.poll(() => batchQueries.some((query) => query.includes('page=3') && query.includes('page_size=50'))).toBe(true);
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

  await page.getByRole('tab', { name: '零件', exact: true }).click();
  await expect(page.getByLabel('跨批次零件号')).toHaveValue('P-900');
  await expect(page.getByText('PL12*280').first()).toBeVisible();
  await expect.poll(() => searchQueries.some((query) => query.includes('page=2') && query.includes('part_no=P-900'))).toBe(true);

  await page.getByRole('button', { name: '清空搜索' }).click();
  await expect(page.getByText('P-900')).toBeHidden();
  const clearedUrl = new URL(page.url());
  expect(clearedUrl.searchParams.has('part_no')).toBe(false);
  expect(clearedUrl.searchParams.has('search_page')).toBe(false);
  expect(clearedUrl.searchParams.get('job_id')).toBe('777');

  await page.getByRole('tab', { name: '五金手册', exact: true }).click();
  await page.getByRole('combobox', { name: '五金手册类别' }).click();
  await page.getByText('角钢', { exact: true }).click();
  await page.getByLabel('钢材规格').fill('L50x5');
  await page.getByRole('button', { name: '查询理论重量' }).click();
  await expect(page.getByText('3.77 kg/m')).toBeVisible();

  await page.getByRole('tab', { name: '处理', exact: true }).click();
  await page.getByRole('button', { name: '预览任务 777 结果' }).click();
  await expect(page.getByRole('dialog', { name: /excel-stage1-777\.xlsx/ })).toBeVisible();
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
  await page.route('**/api/v1/workflows/jobs?**', (route) => route.fulfill({ json: paged([{
    id: 700, task_type: 'extract_dxf_to_excel', status: 'succeeded', progress: 100,
    pipeline: 'dxf2excel', params_json: { batch_name: 'bridge-batch' },
    created_at: '2026-07-13T02:00:00Z', updated_at: '2026-07-13T02:00:02Z',
  }]) }));
  await page.route('**/api/v1/workflows/jobs/700/results**', (route) => route.fulfill({ json: paged([{
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
  await expect(page.getByRole('heading', { name: 'Excel 第一阶段处理' })).toBeVisible();
});
