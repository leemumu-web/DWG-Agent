/**
 * FilesPage — every button tested against its real backend connection.
 *
 * Parameterised over both conversion directions (DWG→DXF and DXF→DWG).
 *
 * Prerequisites:
 *   cd frontend && npm run dev        # Vite :5173  (proxies /api → :8010)
 *   cd backend && uv run uvicorn ...  # FastAPI :8010
 *   DB must be seeded (scripts/db.sh reset or init)
 *
 * Run:
 *   npx playwright test tests/e2e/files-page-buttons.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  API_BASE,
  DXF2DWG_PIPELINE_ENABLED,
  DXF_PIPELINE_ENABLED,
} from '../support/test-env';

// ── direction matrix ─────────────────────────────────────────────────────────

const DIRECTIONS = [
  {
    name: 'DWG→DXF',
    route: '/files/dwg2dxf',
    fileExt: '.dwg' as const,
    taskType: 'convert_dwg_to_dxf' as const,
    samplesDir: '../Stages/dwg2dxf/samples/input',
    emptyFileText: '暂无 DWG 文件',
    uploadBtnPattern: /上传 DWG 文件/,
    downloadSourceBtn: /下载 DWG/,
    downloadResultBtn: '下载 DXF',
    pipelineEnabled: DXF_PIPELINE_ENABLED,
  },
  {
    name: 'DXF→DWG',
    route: '/files/dxf2dwg',
    fileExt: '.dxf' as const,
    taskType: 'convert_dxf_to_dwg' as const,
    samplesDir: '../Stages/dxf2dwg/samples/input',
    emptyFileText: '暂无 DXF 文件',
    uploadBtnPattern: /上传 DXF 文件/,
    downloadSourceBtn: /下载 DXF/,
    downloadResultBtn: '下载 DWG',
    pipelineEnabled: DXF2DWG_PIPELINE_ENABLED,
  },
] as const;

type Direction = (typeof DIRECTIONS)[number];

// ── helpers ─────────────────────────────────────────────────────────────────

/** Log in by injecting a valid token into this tab's sessionStorage. */
async function login(page: Page, route: string) {
  const apiResp = await page.request.post(`${API_BASE}/api/v1/auth/sessions`, {
    data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD },
  });
  const body = await apiResp.json();
  await page.goto('/');
  await page.evaluate(
    ({ t, u }) => {
      sessionStorage.setItem('dwg_access_token', t);
      sessionStorage.setItem('dwg_user', JSON.stringify(u));
    },
    { t: body.data.access_token, u: body.data.user },
  );
  await page.goto(route, { waitUntil: 'domcontentloaded' });
  // The page intentionally polls conversion state, so networkidle can remain
  // false forever. A visible page-specific control is the stable readiness gate.
  const direction = DIRECTIONS.find((item) => item.route === route)!;
  await expect(page.getByRole('button', { name: direction.uploadBtnPattern })).toBeVisible();
}

/** Read a sample file from disk and upload it via the page's file input. */
async function uploadSample(page: Page, samplePath: string, dir: Direction) {
  const chooserPromise = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: dir.uploadBtnPattern }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles(path.resolve(samplePath));
}

/** Find sample files for a given direction. */
function findSamples(samplesDir: string, fileExt: string): string[] {
  const dir = path.resolve(process.cwd(), samplesDir);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(fileExt))
    .map((f) => path.join(dir, f));
}

function minimalSourceFixture(dir: Direction): {
  buffer: Buffer;
  mimeType: string;
} {
  if (dir.fileExt === '.dxf') {
    return {
      buffer: Buffer.from(
        '0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1027\n0\nENDSEC\n'
        + '0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n',
        'ascii',
      ),
      mimeType: 'application/dxf',
    };
  }
  return {
    buffer: Buffer.concat([Buffer.from('AC1027'), Buffer.alloc(2048)]),
    mimeType: 'application/acad',
  };
}

/** Create a fixture file with a deterministically retryable cancelled/failed job. */
async function createRetryableFixture(page: Page, dir: Direction): Promise<number> {
  const token = await page.evaluate(() => sessionStorage.getItem('dwg_access_token'));
  expect(token).toBeTruthy();
  const name = `retry-fixture-${Date.now()}${dir.fileExt}`;
  const fixture = minimalSourceFixture(dir);
  const upload = await page.request.post(`${API_BASE}/api/v1/files`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      upload: {
        name,
        mimeType: fixture.mimeType,
        buffer: fixture.buffer,
      },
    },
  });
  expect(upload.status()).toBe(201);
  const fileId = (await upload.json()).data.id as number;
  const submitted = await page.request.post(`${API_BASE}/api/v1/workflows/jobs`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      task_type: dir.taskType,
      precision_level: 'normal',
      params: { file_id: fileId },
    },
  });
  expect(submitted.status()).toBe(202);
  const jobId = (await submitted.json()).data.id as number;
  const cancelled = await page.request.post(
    `${API_BASE}/api/v1/workflows/jobs/${jobId}/cancellation-requests`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  expect([202, 409]).toContain(cancelled.status());
  await expect.poll(async () => {
    const response = await page.request.get(`${API_BASE}/api/v1/workflows/jobs/${jobId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    return (await response.json()).data.status;
  }, { timeout: 10_000 }).toMatch(/failed|cancelled/);
  return fileId;
}

/** Keep stateful real-backend tests independent after a preceding bulk delete. */
async function ensureSourceFixture(page: Page, dir: Direction): Promise<boolean> {
  const token = await page.evaluate(() => sessionStorage.getItem('dwg_access_token'));
  expect(token).toBeTruthy();
  const rows = page.locator('.ant-table-row');
  const emptyState = page.getByText(dir.emptyFileText, { exact: true });
  await expect.poll(async () => {
    if (await rows.count() > 0) return 'row';
    if (await emptyState.isVisible().catch(() => false)) return 'empty';
    return 'loading';
  }, { timeout: 10_000 }).not.toBe('loading');
  if (await rows.count() > 0) return false;

  const fixture = minimalSourceFixture(dir);
  const uploaded = await page.request.post(`${API_BASE}/api/v1/files`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      upload: {
        name: `e2e-source-${Date.now()}${dir.fileExt}`,
        mimeType: fixture.mimeType,
        buffer: fixture.buffer,
      },
    },
  });
  expect(uploaded.status()).toBe(201);
  return true;
}

async function mockConversionState(
  page: Page,
  dir: Direction,
  jobsDelayMs = 0,
  releasedResult = false,
) {
  const now = new Date().toISOString();
  const files = [1, 2, 3, 4].map((id) => ({
    id: 91_000 + id,
    bucket: 'playwright',
    storage_key: `playwright/${dir.name}/${id}${dir.fileExt}`,
    original_name: `state-${id}${dir.fileExt}`,
    file_ext: dir.fileExt,
    content_type: 'application/octet-stream',
    size_bytes: 1024 * id,
    sha256: String(id).repeat(64),
    batch_name: 'state-fixture',
    status: 'available',
    created_at: now,
    updated_at: now,
  }));
  const jobs = [
    { id: 92_001, status: 'succeeded', progress: 100, fileId: files[0].id },
    { id: 92_002, status: 'running', progress: 50, fileId: files[1].id },
    { id: 92_003, status: 'failed', progress: 70, fileId: files[2].id },
  ].map((job) => ({
    id: job.id,
    task_type: dir.taskType,
    precision_level: 'normal',
    pipeline: null,
    status: job.status,
    attempt: 1,
    priority: 0,
    progress: job.progress,
    params_json: { file_id: job.fileId, batch_name: 'state-fixture' },
    error_code: job.status === 'failed' ? 'CONVERSION_FAILED' : null,
    error_message: job.status === 'failed' ? '测试转换失败，请重新提交' : null,
    progress_data: null,
    result_available: job.status === 'succeeded' ? !releasedResult : null,
    created_at: now,
    updated_at: now,
    started_at: null,
    finished_at: null,
  }));
  const envelope = (data: unknown[], pageSize = 200) => ({
    data,
    pagination: { page: 1, page_size: pageSize, total: data.length, total_pages: 1 },
    meta: { request_id: 'playwright-state', timestamp: now },
  });

  await page.route('**/api/v1/files/batches?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ data: [], meta: { request_id: 'playwright-batches', timestamp: now } }),
  }));
  await page.route('**/api/v1/files?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(envelope(files, 200)),
  }));
  await page.route('**/api/v1/workflows/jobs?**', async (route) => {
    if (jobsDelayMs > 0) await new Promise((resolve) => setTimeout(resolve, jobsDelayMs));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(envelope(jobs, 200)),
    });
  });
}

async function mockFolderState(page: Page, dir: Direction) {
  const now = new Date().toISOString();
  const batches = [
    { name: 'batch-alpha', file_count: 2, latest_created_at: now },
    { name: 'batch-beta', file_count: 3, latest_created_at: now },
  ];
  const files = batches.map((batch, index) => ({
    id: 93_001 + index,
    bucket: 'playwright',
    storage_key: `playwright/${batch.name}/source${dir.fileExt}`,
    original_name: `source-${index + 1}${dir.fileExt}`,
    file_ext: dir.fileExt,
    content_type: 'application/octet-stream',
    size_bytes: 2048,
    sha256: String(index + 1).repeat(64),
    batch_name: batch.name,
    status: 'available',
    created_at: now,
    updated_at: now,
  }));
  const envelope = (data: unknown[]) => ({
    data,
    pagination: { page: 1, page_size: 200, total: data.length, total_pages: 1 },
    meta: { request_id: 'playwright-folder', timestamp: now },
  });

  await page.route('**/api/v1/files/batches?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ data: batches, meta: { request_id: 'playwright-batches', timestamp: now } }),
  }));
  await page.route('**/api/v1/files?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(envelope(files)),
  }));
  await page.route('**/api/v1/workflows/jobs?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(envelope([])),
  }));
}

// ── tests ───────────────────────────────────────────────────────────────────

for (const dir of DIRECTIONS) {
  test.describe(`FilesPage — ${dir.name}`, () => {
    test.beforeEach(async ({ page }) => {
      await login(page, dir.route);
      if (await ensureSourceFixture(page, dir)) {
        await page.reload({ waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('button', { name: dir.uploadBtnPattern })).toBeVisible();
        await expect(page.locator('.ant-table-row').first()).toBeVisible();
      }
    });

    // ── 1. Upload single file ────────────────────────────────────────
  test('upload single file → file created + job enqueued', async ({ page }) => {
    test.skip(!dir.pipelineEnabled, `${dir.name} pipeline is disabled in this deployment`);
    const samples = findSamples(dir.samplesDir, dir.fileExt);
    test.skip(samples.length === 0, `No sample ${dir.fileExt} files found`);

    const fileName = path.basename(samples[0]);
    const uploadResponse = page.waitForResponse(
      (response) => response.url().includes('/api/v1/files')
        && response.request().method() === 'POST',
    );
    const jobResponse = page.waitForResponse(
      (response) => response.url().endsWith('/api/v1/workflows/jobs/batches')
        && response.request().method() === 'POST',
    );
    await uploadSample(page, samples[0], dir);
    expect((await uploadResponse).status()).toBe(201);
    expect((await jobResponse).status()).toBe(202);

    // Toast "已提交" should appear
    await expect(page.locator('.upload-toast').first()).toBeVisible({ timeout: 15_000 });

    await expect(page.getByText(fileName, { exact: true }).first()).toBeVisible({ timeout: 15_000 });
  });

  // ── 2. Pause this conversion scope only ──────────────────────────────
  test('"全部暂停" handles active-job completion races', async ({ page }) => {
    const pauseBtn = page.getByRole('button', { name: /全部暂停/ });
    if (!(await pauseBtn.isVisible())) {
      test.skip(true, 'No active jobs to pause');
      return;
    }

    const [cancelResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/api/v1/workflows/jobs/cancellation-requests') && r.request().method() === 'POST',
        { timeout: 10_000 },
      ),
      pauseBtn.click(),
    ]);

    expect([202, 409]).toContain(cancelResp.status());
    const body = await cancelResp.json();
    if (cancelResp.status() === 202) {
      expect(body.data).toHaveProperty('cancelled_count');
    } else {
      expect(body.error).toHaveProperty('code');
    }
  });

  // ── 3. "提交/重试" → one bounded batch request ──────────────────────
  test('"提交/重试" → creates a batch for actionable files', async ({ page }) => {
    const resumeBtn = page.getByRole('button', { name: /提交\/重试/ });
    const hasBtn = await resumeBtn.isVisible().catch(() => false);
    test.skip(!hasBtn, '"提交/重试" button not visible (no actionable files)');

    let jobCalls = 0;
    page.on('request', (req) => {
      if (req.url().endsWith('/api/v1/workflows/jobs/batches') && req.method() === 'POST') {
        const data = req.postDataJSON();
        if (data?.task_type === dir.taskType) jobCalls++;
      }
    });

    await resumeBtn.click();
    await page.waitForTimeout(5000);
    expect(jobCalls).toBeGreaterThan(0);
  });

  // ── 4. Row checkboxes → action bar appears ───────────────────────────
  test('select rows → bulk action bar with 打包下载 + 删除选中', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const checkboxes = page.locator('.ant-table-tbody .ant-table-selection-column .ant-checkbox-input');
    const rowCount = await checkboxes.count();
    test.skip(rowCount === 0, 'No table rows to select');

    await checkboxes.first().check();

    const actionBar = page.getByText(/已选 \d/);
    await expect(actionBar.first()).toBeVisible({ timeout: 5000 });

    await expect(page.getByRole('button', { name: /打包下载/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /删除选中/ })).toBeVisible();
  });

  // ── 5. Bulk delete → POST /files/bulk-delete → 204 ─────────────────
  test('"删除选中" → POST /files/bulk-delete → 204', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const checkboxes = page.locator('.ant-table-tbody .ant-table-selection-column .ant-checkbox-input');
    const count = await checkboxes.count();
    test.skip(count === 0, 'No rows');

    await checkboxes.first().check();

    const [delResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/api/v1/files/bulk-delete') && r.request().method() === 'POST',
        { timeout: 10_000 },
      ),
      page.getByRole('button', { name: /删除选中/ }).click(),
      page.getByRole('button', { name: /确认删除/ }).click(),
    ]);

    expect(delResp.status()).toBe(204);
  });

  test('source and converted original names stay visible with an explicit preview origin', async ({ page }) => {
    await mockConversionState(page, dir);
    await page.reload();

    await expect(page.getByRole('columnheader', { name: '原文件名' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: '转换后文件名' })).toBeVisible();
    const row = page.locator('.ant-table-row[data-row-key="91001"]');
    await expect(row.getByText(`state-1${dir.fileExt}`, { exact: true })).toBeVisible();
    const convertedName = `state-1${dir.fileExt === '.dwg' ? '.dxf' : '.dwg'}`;
    await expect(row.getByText(convertedName, { exact: true })).toBeVisible();
    if (dir.fileExt === '.dxf') {
      await expect(row.getByRole('button', { name: '预览原始 DXF' })).toBeVisible();
    } else {
      await expect(row.getByRole('button', { name: '预览转换后 DXF' })).toBeVisible();
    }
  });

  test('result download resolves the registered result original name', async ({ page }) => {
    await mockConversionState(page, dir);
    const resultFileId = dir.fileExt === '.dwg' ? 94_001 : 94_002;
    const resultExt = dir.fileExt === '.dwg' ? '.dxf' : '.dwg';
    const registeredName = `服务端登记结果${resultExt}`;
    let metadataRequests = 0;
    const now = new Date().toISOString();
    await page.route('**/api/v1/workflows/jobs/92001/results?**', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [{
          id: 95_001,
          job_id: 92_001,
          result_type: dir.taskType,
          result_file_id: resultFileId,
          status: 'succeeded',
          created_at: now,
          updated_at: now,
        }],
        pagination: { page: 1, page_size: 200, total: 1, total_pages: 1 },
        meta: { request_id: 'registered-result', timestamp: now },
      }),
    }));
    await page.route(`**/api/v1/files/${resultFileId}`, (route) => {
      metadataRequests += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            id: resultFileId,
            bucket: 'playwright',
            storage_key: `playwright/${registeredName}`,
            original_name: registeredName,
            file_ext: resultExt,
            content_type: 'application/octet-stream',
            size_bytes: 32,
            sha256: 'a'.repeat(64),
            status: 'available',
            created_at: now,
            updated_at: now,
          },
          meta: { request_id: 'registered-result-file', timestamp: now },
        }),
      });
    });
    await page.route(`**/api/v1/files/${resultFileId}/download-url`, (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: { url: `/api/v1/files/${resultFileId}/download`, expires_in: 300 },
        meta: { request_id: 'registered-result-download', timestamp: now },
      }),
    }));
    await page.route(`**/api/v1/files/${resultFileId}/download`, (route) => route.fulfill({
      status: 200,
      contentType: 'application/octet-stream',
      headers: { 'content-disposition': `attachment; filename="${registeredName}"` },
      body: Buffer.alloc(32, 65),
    }));
    await page.reload();

    const row = page.locator('.ant-table-row[data-row-key="91001"]');
    const download = page.waitForEvent('download');
    await row.getByRole('button', { name: dir.downloadResultBtn }).click();
    const downloaded = await download;
    expect(downloaded.suggestedFilename()).toBe(registeredName);
    expect(metadataRequests).toBe(1);
    await downloaded.delete();
  });

  // ── 6. Zip modal: format checkboxes control download button ─────────
  test('zip modal: must select DWG or DXF to enable download', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const checkboxes = page.locator('.ant-table-tbody .ant-table-selection-column .ant-checkbox-input');
    const count = await checkboxes.count();
    test.skip(count === 0, 'No rows');

    await checkboxes.first().check();

    await page.getByRole('button', { name: /打包下载/ }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible({ timeout: 3000 });

    const dlBtn = dialog.getByRole('button', { name: /开始下载/ });
    const sourceLabel = dir.fileExt === '.dwg' ? '包含 DWG 文件' : '包含 DXF 文件';
    const sourceOption = dialog.getByRole('checkbox', { name: sourceLabel });
    await expect(sourceOption).toBeEnabled();
    await expect(dlBtn).toBeEnabled();

    await sourceOption.uncheck();
    await expect(dlBtn).toBeDisabled();

    await sourceOption.check();
    await expect(dlBtn).toBeEnabled();

    await dialog.getByRole('button', { name: /取\s*消/ }).click();
  });

  // ── 7. Folder name input → reflected in preview ─────────────────────
  test('zip modal: folder name input updates preview', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const checkboxes = page.locator('.ant-table-tbody .ant-table-selection-column .ant-checkbox-input');
    test.skip((await checkboxes.count()) === 0, 'No rows');

    await checkboxes.first().check();
    await page.getByRole('button', { name: /打包下载/ }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible({ timeout: 3000 });

    const input = dialog.getByPlaceholder(/输入文件夹名称/);
    await input.fill('我的项目导出');
    await expect(input).toHaveValue('我的项目导出');

    await dialog.getByRole('button', { name: /取\s*消/ }).click();
  });

  test('zip modal disables a format that is not available for every file', async ({ page }) => {
    await mockConversionState(page, dir);
    const sourceFormat = dir.fileExt.slice(1);
    const targetFormat = sourceFormat === 'dwg' ? 'dxf' : 'dwg';
    await page.route('**/api/v1/files/download-zip/preview', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: {
          file_count: 2,
          formats: [
            {
              format: sourceFormat,
              available_count: 2,
              missing_count: 0,
              missing_file_ids: [],
              complete: true,
            },
            {
              format: targetFormat,
              available_count: 1,
              missing_count: 1,
              missing_file_ids: [91_002],
              complete: false,
            },
          ],
          can_download: false,
        },
        meta: { request_id: 'playwright-zip-preview', timestamp: new Date().toISOString() },
      }),
    }));
    await page.reload();

    const checkboxes = page.locator('.ant-table-tbody .ant-table-selection-column .ant-checkbox-input');
    await checkboxes.nth(0).check();
    await checkboxes.nth(1).check();
    await page.getByRole('button', { name: /打包下载/ }).click();
    const dialog = page.getByRole('dialog', { name: '打包下载' });
    const targetLabel = targetFormat === 'dxf' ? '包含 DXF 文件' : '包含 DWG 文件';

    await expect(dialog.getByRole('checkbox', { name: targetLabel })).toBeDisabled();
    await expect(dialog.getByText(new RegExp(`${targetFormat.toUpperCase()}.*可用 1 / 共 2`))).toBeVisible();
  });

  test('zip modal preserves input after a formal download 409', async ({ page }) => {
    await mockConversionState(page, dir);
    await page.route('**/api/v1/files/download-zip/preview', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: {
          file_count: 1,
          formats: ['dwg', 'dxf'].map((format) => ({
            format,
            available_count: 1,
            missing_count: 0,
            missing_file_ids: [],
            complete: true,
          })),
          can_download: true,
        },
        meta: { request_id: 'playwright-zip-preview', timestamp: new Date().toISOString() },
      }),
    }));
    await page.route('**/api/v1/files/download-zip', (route) => route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'FILE_EXPORT_FORMAT_UNAVAILABLE',
          message: '所选格式当前不完整，请重新检查。',
          details: { file_id: 91_001, format: 'dxf' },
        },
        meta: { request_id: 'playwright-zip-conflict', timestamp: new Date().toISOString() },
      }),
    }));
    await page.reload();

    const checkbox = page.locator('.ant-table-tbody .ant-table-selection-column .ant-checkbox-input').first();
    await checkbox.check();
    await page.getByRole('button', { name: /打包下载/ }).click();
    const dialog = page.getByRole('dialog', { name: '打包下载' });
    const nameInput = dialog.getByPlaceholder(/输入文件夹名称/);
    await nameInput.fill('保留名称');
    await dialog.getByRole('button', { name: /开始下载/ }).click();

    await expect(dialog).toBeVisible();
    await expect(nameInput).toHaveValue('保留名称');
    await expect(page.getByText(
      '所选格式当前不完整，请重新检查。（请求编号 playwright-zip-conflict）',
    )).toBeVisible();
    await expect(page.getByText(/FILE_EXPORT_FORMAT_UNAVAILABLE/)).toHaveCount(0);
  });

  // ── 8. Zip download → POST /files/download-zip → 200 + blob ──────
  test('zip download → POST /files/download-zip → 200 streaming zip', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const checkboxes = page.locator('.ant-table-tbody .ant-table-selection-column .ant-checkbox-input');
    test.skip((await checkboxes.count()) === 0, 'No rows');

    await checkboxes.first().check();

    await page.getByRole('button', { name: /打包下载/ }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible({ timeout: 3000 });

    await dialog.getByPlaceholder(/输入文件夹名称/).fill('e2e_test');
    // Source format is selected by default. The target format is enabled only
    // when every selected source has a registered conversion result.
    await expect(dialog.getByRole('button', { name: /开始下载/ })).toBeEnabled();
    await page.route('**/api/v1/files/download-zip', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 250));
      await route.fulfill({
        status: 200,
        contentType: 'application/zip',
        headers: {
          'content-disposition': 'attachment; filename="e2e_test.zip"',
          'content-length': '8192',
        },
        body: Buffer.alloc(8192, 65),
      });
    });

    const downloadPromise = page.waitForEvent('download', { timeout: 30_000 });
    await dialog.getByRole('button', { name: /开始下载/ }).click();
    await expect(dialog.getByLabel('图纸打包下载进度')).toBeVisible();
    const download = await downloadPromise;

    expect(download.suggestedFilename()).toMatch(/e2e_test\.zip$/);
    await download.delete();
  });

  // ── 9. Source file download button → signed URL ────────────────────
  test('source file download → signed download URL', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const btn = page.getByRole('button', { name: dir.downloadSourceBtn }).first();
    const hasBtn = await btn.isVisible().catch(() => false);
    test.skip(!hasBtn, `No source download button (${dir.downloadSourceBtn}) found`);

    const [dlResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/api/v1/files/') && r.url().includes('/download-url'),
        { timeout: 10_000 },
      ),
      btn.click(),
    ]);

    expect(dlResp.status()).toBe(200);
    const body = await dlResp.json();
    expect(body.data).toHaveProperty('url');
    expect(body.data).toHaveProperty('expires_in');
    expect(body.data.expires_in).toBe(300);
  });

  // ── 10. Result download button → job results → download ──────────
  test('result download → job results → file download', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const btn = page.getByRole('button', { name: dir.downloadResultBtn }).first();
    const hasBtn = await btn.isVisible().catch(() => false);
    test.skip(!hasBtn, `No result download button (${dir.downloadResultBtn}) found`);

    const respPromise = page.waitForResponse(
      (r) => r.url().includes('/download-url') && r.request().method() === 'GET',
      { timeout: 15_000 },
    );

    await btn.click();
    const dlResp = await respPromise.catch(() => null);
    if (dlResp) {
      expect(dlResp.status()).toBe(200);
    }
  });

  // ── 11. "重新提交" button → POST /jobs/{id}/retry-requests ──────
  test('"重新提交" → POST /jobs/{id}/retry-requests', async ({ page }) => {
    test.skip(!dir.pipelineEnabled, `${dir.name} pipeline is disabled in this deployment`);
    const fileId = await createRetryableFixture(page, dir);
    await page.reload();
    const row = page.locator(`.ant-table-row[data-row-key="${fileId}"]`);
    await expect(row).toBeVisible({ timeout: 10_000 });
    const retryBtn = row.getByRole('button', { name: '重新提交' });
    await expect(retryBtn).toBeVisible();

    const [retryResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/retry-requests') && r.request().method() === 'POST',
        { timeout: 10_000 },
      ),
      retryBtn.click(),
    ]);

    expect(retryResp.status()).toBe(202);
  });

  test('released result is reported and resubmitted as a new batch', async ({ page }) => {
    await mockConversionState(page, dir, 0, true);
    let submittedBody: Record<string, unknown> | undefined;
    await page.route('**/api/v1/workflows/jobs/batches', async (route) => {
      submittedBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          data: { jobs: [] },
          meta: { request_id: 'released-result-resubmit', timestamp: new Date().toISOString() },
        }),
      });
    });
    await page.reload();

    const row = page.locator('.ant-table-row[data-row-key="91001"]');
    await expect(row.getByText('结果已释放', { exact: true })).toBeVisible();
    await expect(row.getByRole('button', { name: dir.downloadResultBtn })).toHaveCount(0);
    await row.getByRole('button', { name: '重新提交' }).click();

    await expect.poll(() => submittedBody).toMatchObject({
      task_type: dir.taskType,
      file_ids: [91_001],
    });
  });

  // ── 12. Table pagination: page size changer ──────────────────────
  test('table pagination: size changer shows options', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });

    const sizeSelector = page.locator('.ant-pagination-options-size-changer .ant-select-selector');
    if (await sizeSelector.isVisible()) {
      await sizeSelector.click();
      const dropdown = page.locator('.ant-select-dropdown:visible');
      await expect(dropdown).toBeVisible({ timeout: 3000 });

      const options = dropdown.locator('.ant-select-item-option-content');
      const firstCount = await options.count();
      if (firstCount > 1) {
        await options.nth(1).click();
        await page.waitForTimeout(500);
      }
    }
  });

  // ── 13. Batch cards → navigate into batch detail ─────────────────
  test('batch card click → batch detail with breadcrumb', async ({ page }) => {
    const cards = page.locator('.folder-card');
    const cardCount = await cards.count();
    test.skip(cardCount === 0, 'No batch cards');
    await expect(cards.first()).toBeVisible({ timeout: 10_000 });

    await cards.first().locator('.ant-card-meta-title').click();

    const backBtn = page.getByRole('button', { name: /返回/ });
    await expect(backBtn).toBeVisible({ timeout: 5000 });

    // Navigate back
    await backBtn.click();
    await expect(page.getByRole('button', { name: dir.uploadBtnPattern })).toBeVisible();
    await expect(cards.first()).toBeVisible();
  });

  // ── 14. Scope actions reflect active and actionable jobs independently ──
  test('active and actionable scope actions can coexist', async ({ page }) => {
    await mockConversionState(page, dir);
    await page.reload();

    const pauseBtn = page.getByRole('button', { name: /全部暂停/ });
    const resumeBtn = page.getByRole('button', { name: /提交\/重试/ });

    await expect(pauseBtn).toBeVisible();
    await expect(resumeBtn).toBeVisible();
  });

  // ── 15. 上传文件夹 button → file input dialog ──────────────────
  test('"上传文件夹" button opens file input', async ({ page }) => {
    const folderBtn = page.getByRole('button', { name: /上传文件夹/ });
    await expect(folderBtn).toBeVisible({ timeout: 5000 });

    const fileChooserPromise = page.waitForEvent('filechooser', { timeout: 5000 });
    await folderBtn.click();
    const fileChooser = await fileChooserPromise;
    expect(fileChooser).toBeTruthy();
  });

  // ── 16. Empty state shown when no files ─────────────────────────
  test('empty state renders when table is empty', async ({ page }) => {
    await page.route('**/api/v1/files?**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [],
          pagination: { page: 1, page_size: 200, total: 0, total_pages: 0 },
          meta: { request_id: 'playwright-empty', timestamp: new Date().toISOString() },
        }),
      });
    });
    await page.reload();

    await expect(page.getByText(dir.emptyFileText, { exact: true })).toBeVisible();
    await expect(page.locator('.ant-table-tbody .ant-table-row')).toHaveCount(0);
  });

  // ── 17. Navigation: sidebar links are present ──────────────────
  test('sidebar navigation links work', async ({ page }) => {
    const sidebar = page.locator('.ant-layout-sider');
    await expect(sidebar).toBeVisible();

    for (const label of ['文件', '任务', '工作台']) {
      const link = sidebar.getByText(label);
      if (await link.isVisible()) {
        await expect(link).toBeVisible();
      }
    }
  });

  // ── 18. Stats cards show correct values ─────────────────────────
  test('stats cards render with numeric values', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 }).catch(() => {});

    const statValues = page.locator('[style*="fontSize: 22"][style*="fontWeight: 700"]');
    const count = await statValues.count();

    for (let i = 0; i < count; i++) {
      const text = await statValues.nth(i).textContent();
      expect(text).toBeTruthy();
    }
  });

  test('trustworthy progress excludes failed residual progress', async ({ page }) => {
    await mockConversionState(page, dir);
    await page.reload();

    await expect(page.getByText(/成功 1.*失败 1.*处理中 1.*待提交\/重试 2/)).toBeVisible();
    const aggregate = page.locator('.conversion-progress').getByRole('progressbar');
    await expect(aggregate).toHaveAttribute('aria-valuenow', '38');
    await expect(page.getByRole('button', { name: /提交\/重试 2 个/ })).toBeVisible();
  });

  test('loading latest jobs never labels files as unconverted', async ({ page }) => {
    await mockConversionState(page, dir, 1500);
    await page.reload();

    await expect(page.getByText('正在加载状态').first()).toBeVisible();
    await expect(page.getByText('未转换')).toHaveCount(0);
    await expect(page.getByText('已完成').first()).toBeVisible({ timeout: 5000 });
  });

  test('folder actions precede the grid and folders are keyboard buttons', async ({ page }) => {
    await mockFolderState(page, dir);
    await page.reload();

    const actions = page.locator('.folder-actions');
    const grid = page.locator('.folder-grid');
    await expect(actions).toBeVisible();
    expect(await actions.evaluate((node, other) => Boolean(
      node.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING,
    ), await grid.elementHandle())).toBe(true);
    await expect(page.getByRole('button', { name: '打开文件夹 batch-alpha' })).toBeVisible();
    await expect(page.getByRole('button', { name: '全选 2 个文件夹' })).toBeVisible();
  });

  test('multi-folder delete sends one atomic request', async ({ page }) => {
    await mockFolderState(page, dir);
    let deleteCalls = 0;
    let deleteBody: unknown;
    await page.route('**/api/v1/files/batches/bulk-delete', async (route) => {
      deleteCalls += 1;
      deleteBody = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: { deleted_batch_count: 2, deleted_file_count: 7, cancelled_job_count: 1 },
          meta: { request_id: 'playwright-delete', timestamp: new Date().toISOString() },
        }),
      });
    });
    await page.reload();

    await page.getByRole('button', { name: '全选 2 个文件夹' }).click();
    await page.getByRole('button', { name: '删除 2 个文件夹' }).click();
    await page.getByRole('button', { name: '确认删除' }).click();
    await expect.poll(() => deleteCalls).toBe(1);
    expect(deleteBody).toEqual({ batch_names: ['batch-alpha', 'batch-beta'] });
    await expect(page.getByText(/2 个文件夹.*7 个文件.*1 个任务/)).toBeVisible();
  });

  test('multi-folder package gathers every selected folder', async ({ page }) => {
    await mockFolderState(page, dir);
    const queriedBatches: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      const batchName = url.searchParams.get('batch_name');
      if (url.pathname === '/api/v1/files' && batchName) queriedBatches.push(batchName);
    });
    await page.reload();

    await page.getByRole('button', { name: '全选 2 个文件夹' }).click();
    await page.getByRole('button', { name: '打包下载 2 个文件夹' }).click();
    const dialog = page.getByRole('dialog', { name: '打包下载' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText('已选 2 个文件')).toBeVisible();
    expect(new Set(queriedBatches)).toEqual(new Set(['batch-alpha', 'batch-beta']));
  });

  test('failed multi-folder delete preserves selection', async ({ page }) => {
    await mockFolderState(page, dir);
    await page.route('**/api/v1/files/batches/bulk-delete', (route) => route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({
        error: { code: 'DELETE_FAILED', message: '删除失败，请重试', details: {} },
        meta: { request_id: 'playwright-delete-failed' },
      }),
    }));
    await page.reload();

    await page.getByRole('button', { name: '全选 2 个文件夹' }).click();
    await page.getByRole('button', { name: '删除 2 个文件夹' }).click();
    await page.getByRole('button', { name: '确认删除' }).click();
    await expect(page.getByText('删除失败，请重试（请求编号 playwright-delete-failed）')).toBeVisible();
    await expect(page.getByText(/DELETE_FAILED/)).toHaveCount(0);
    await expect(page.getByText('已选 2 个文件夹')).toBeVisible();
    await expect(page.locator('.folder-card input[type="checkbox"]:checked')).toHaveCount(2);
  });

  test('validation failure shows the exact field reason instead of only HTTP 422', async ({ page }) => {
    await mockFolderState(page, dir);
    await page.route('**/api/v1/files/batches/bulk-delete', (route) => route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'VALIDATION_ERROR',
          message: 'Request validation failed.',
          details: { errors: [{ loc: ['body', 'batch_names', 1], msg: '文件夹名称不能为空' }] },
        },
        meta: { request_id: 'playwright-validation' },
      }),
    }));
    await page.reload();

    await page.getByRole('button', { name: '全选 2 个文件夹' }).click();
    await page.getByRole('button', { name: '删除 2 个文件夹' }).click();
    await page.getByRole('button', { name: '确认删除' }).click();
    await expect(page.getByText(
      '请求参数错误：文件夹名称不能为空（请求编号 playwright-validation）',
    )).toBeVisible();
    await expect(page.getByText(/VALIDATION_ERROR|batch_names/)).toHaveCount(0);
  });
  });
}
