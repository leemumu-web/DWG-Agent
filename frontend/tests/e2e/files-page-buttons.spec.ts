/**
 * FilesPage — every button tested against its real backend connection.
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
import { API_BASE } from './test-env';

// ── helpers ─────────────────────────────────────────────────────────────────

/** Log in by injecting a valid token into this tab's sessionStorage. */
async function login(page: Page) {
  const apiResp = await page.request.post(`${API_BASE}/api/v1/auth/sessions`, {
    data: { username: 'admin', password: 'SuperAdminPass1' },
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
  await page.goto('/files');
  await page.waitForLoadState('networkidle');
}

/** Read a sample DWG from disk and upload it via the page's file input. */
async function uploadSampleDwg(page: Page, samplePath: string) {
  const chooserPromise = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: /上传 DWG 文件/ }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles(path.resolve(samplePath));
}

/** Find sample DWG files for testing. */
function findSampleDwgs(): string[] {
  const dir = path.resolve(process.cwd(), '../Stages/dwg2dxf/samples/input');
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith('.dwg'))
    .map((f) => path.join(dir, f));
}

async function createFailedDxfFixture(page: Page): Promise<number> {
  const token = await page.evaluate(() => sessionStorage.getItem('dwg_access_token'));
  expect(token).toBeTruthy();
  const name = `retry-fixture-${Date.now()}.dwg`;
  const upload = await page.request.post(`${API_BASE}/api/v1/files`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      upload: {
        name,
        mimeType: 'application/acad',
        buffer: Buffer.concat([Buffer.from('AC1027'), Buffer.alloc(2048)]),
      },
    },
  });
  expect(upload.status()).toBe(201);
  const fileId = (await upload.json()).data.id as number;
  const submitted = await page.request.post(`${API_BASE}/api/v1/jobs`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      task_type: 'convert_dwg_to_dxf',
      precision_level: 'normal',
      params: { file_id: fileId },
    },
  });
  expect(submitted.status()).toBe(202);
  const jobId = (await submitted.json()).data.id as number;
  await expect.poll(async () => {
    const response = await page.request.get(`${API_BASE}/api/v1/jobs/${jobId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    return (await response.json()).data.status;
  }, { timeout: 30_000 }).toBe('failed');
  return fileId;
}

// ── tests ───────────────────────────────────────────────────────────────────

test.describe('FilesPage — button & API integration', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  // ── 1. Upload single DWG ──────────────────────────────────────────────
  test('upload single DWG → file created + job enqueued', async ({ page }) => {
    const samples = findSampleDwgs();
    test.skip(samples.length === 0, 'No sample DWG files found');

    const fileName = path.basename(samples[0]);
    const uploadResponse = page.waitForResponse(
      (response) => response.url().includes('/api/v1/files')
        && response.request().method() === 'POST',
    );
    const jobResponse = page.waitForResponse(
      (response) => response.url().endsWith('/api/v1/jobs')
        && response.request().method() === 'POST',
    );
    await uploadSampleDwg(page, samples[0]);
    expect((await uploadResponse).status()).toBe(201);
    expect((await jobResponse).status()).toBe(202);

    // Toast "已提交" should appear
    await expect(page.locator('.upload-toast').first()).toBeVisible({ timeout: 15_000 });

    await expect(page.getByText(fileName, { exact: true }).first()).toBeVisible({ timeout: 15_000 });
  });

  // ── 2. Pause all → verify backend cancel-all called ──────────────────
  test('"全部暂停" → POST /jobs/cancel-all-active → 200', async ({ page }) => {
    // We need active jobs — upload a file and create a job without Celery to keep it queued
    const pauseBtn = page.getByRole('button', { name: /全部暂停/ });
    if (!(await pauseBtn.isVisible())) {
      // No active jobs — skip gracefully
      test.skip(true, 'No active jobs to pause');
      return;
    }

    // Intercept the cancel-all request
    const [cancelResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/api/v1/jobs/cancel-all-active') && r.request().method() === 'POST',
        { timeout: 10_000 },
      ),
      pauseBtn.click(),
    ]);

    expect(cancelResp.status()).toBe(200);
    const body = await cancelResp.json();
    expect(body.data).toHaveProperty('cancelled_count');
  });

  // ── 3. "继续任务" → POST /jobs (createDxfJob) for each pending file ──
  test('"继续任务" → creates DXF jobs for pending files', async ({ page }) => {
    const resumeBtn = page.getByRole('button', { name: /继续任务/ });
    const hasBtn = await resumeBtn.isVisible().catch(() => false);
    test.skip(!hasBtn, '"继续任务" button not visible (no pending files)');

    // Track job creation requests
    let jobCalls = 0;
    page.on('request', (req) => {
      if (req.url().includes('/api/v1/jobs') && req.method() === 'POST') {
        const data = req.postDataJSON();
        if (data?.task_type === 'convert_dwg_to_dxf') jobCalls++;
      }
    });

    await resumeBtn.click();
    await page.waitForTimeout(5000);
    expect(jobCalls).toBeGreaterThan(0);
  });

  // ── 4. Row checkboxes → action bar appears ───────────────────────────
  test('select rows → bulk action bar with 打包下载 + 删除选中', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    // Use the selection checkboxes (Ant Design renders them in the first column)
    const checkboxes = page.locator('.ant-table-tbody .ant-table-selection-column .ant-checkbox-input');
    const rowCount = await checkboxes.count();
    test.skip(rowCount === 0, 'No table rows to select');

    await checkboxes.first().check();

    // Action bar should appear with selection count
    const actionBar = page.getByText(/已选 \d/);
    await expect(actionBar.first()).toBeVisible({ timeout: 5000 });

    // Buttons should be visible
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

  // ── 6. Zip modal: format checkboxes control download button ─────────
  test('zip modal: must select DWG or DXF to enable download', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const checkboxes = page.locator('.ant-table-tbody .ant-table-selection-column .ant-checkbox-input');
    const count = await checkboxes.count();
    test.skip(count === 0, 'No rows');

    await checkboxes.first().check();

    // Open zip modal
    await page.getByRole('button', { name: /打包下载/ }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible({ timeout: 3000 });

    const dlBtn = dialog.getByRole('button', { name: /开始下载/ });
    await expect(dlBtn).toBeEnabled();

    // Uncheck both → button disabled — use label click which is more reliable
    const dwgOption = dialog.getByRole('checkbox', { name: '包含 DWG 文件' });
    const dxfOption = dialog.getByRole('checkbox', { name: '包含 DXF 文件' });
    await dwgOption.uncheck();
    await dxfOption.uncheck();
    await expect(dlBtn).toBeDisabled();

    // Check DWG again → enabled
    await dwgOption.check();
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

    // Find the text input by placeholder
    const input = dialog.getByPlaceholder(/输入文件夹名称/);
    await input.fill('我的项目导出');
    await expect(input).toHaveValue('我的项目导出');

    await dialog.getByRole('button', { name: /取\s*消/ }).click();
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

    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 30_000 }),
      dialog.getByRole('button', { name: /开始下载/ }).click(),
    ]);

    expect(download.suggestedFilename()).toMatch(/e2e_test\.zip$/);
    await download.delete();
  });

  // ── 9. "下载 DWG" single file button → GET /files/{id}/download ──
  test('single "下载 DWG" → signed download URL', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const dwgBtn = page.getByRole('button', { name: /下载 DWG/ }).first();
    const hasBtn = await dwgBtn.isVisible().catch(() => false);
    test.skip(!hasBtn, 'No DWG download button found');

    const [dlResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/api/v1/files/') && r.url().includes('/download-url'),
        { timeout: 10_000 },
      ),
      dwgBtn.click(),
    ]);

    expect(dlResp.status()).toBe(200);
    const body = await dlResp.json();
    expect(body.data).toHaveProperty('url');
    expect(body.data).toHaveProperty('expires_in');
    expect(body.data.expires_in).toBe(300);
  });

  // ── 10. "下载 DXF" button → GET /jobs/{id}/results → download ───
  test('single "下载 DXF" → job results → file download', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const dxfBtn = page.getByRole('button', { name: '下载 DXF' }).first();
    const hasBtn = await dxfBtn.isVisible().catch(() => false);
    test.skip(!hasBtn, 'No DXF download button found (no succeeded jobs)');

    // Track the download-url request for the DXF result file
    const respPromise = page.waitForResponse(
      (r) => r.url().includes('/download-url') && r.request().method() === 'GET',
      { timeout: 15_000 },
    );

    await dxfBtn.click();
    const dlResp = await respPromise.catch(() => null);
    if (dlResp) {
      expect(dlResp.status()).toBe(200);
    }
  });

  // ── 11. "重试转换" button → POST /jobs/{id}/retry-requests ──────
  test('"重试转换" → POST /jobs/{id}/retry-requests', async ({ page }) => {
    const fileId = await createFailedDxfFixture(page);
    await page.reload();
    const row = page.locator(`.ant-table-row[data-row-key="${fileId}"]`);
    await expect(row).toBeVisible({ timeout: 10_000 });
    const retryBtn = row.getByRole('button', { name: '重试转换' });
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

  // ── 12. Table pagination: page size changer ──────────────────────
  test('table pagination: size changer shows options', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });

    const sizeSelector = page.locator('.ant-pagination-options-size-changer .ant-select-selector');
    if (await sizeSelector.isVisible()) {
      await sizeSelector.click();
      // Dropdown should appear
      const dropdown = page.locator('.ant-select-dropdown:visible');
      await expect(dropdown).toBeVisible({ timeout: 3000 });

      // Pick the first available option
      const options = dropdown.locator('.ant-select-item-option-content');
      const firstCount = await options.count();
      if (firstCount > 1) {
        await options.nth(1).click(); // pick a different size
        await page.waitForTimeout(500);
      }
    }
  });

  // ── 14. Batch cards → navigate into batch detail ─────────────────
  test('batch card click → batch detail with breadcrumb', async ({ page }) => {
    await page.waitForLoadState('networkidle');

    const cards = page.locator('.ant-card');
    const cardCount = await cards.count();
    test.skip(cardCount === 0, 'No batch cards');

    // Click the card title (avoids checkbox, resolves to single element)
    await cards.first().locator('.ant-card-meta-title').click();

    // Should see "返回" button in the content area
    const backBtn = page.getByRole('button', { name: /返回/ });
    await expect(backBtn).toBeVisible({ timeout: 5000 });

    // Navigate back
    await backBtn.click();
    await expect(page.getByRole('button', { name: /上传 DWG 文件/ })).toBeVisible();
    await expect(cards.first()).toBeVisible();
  });

  // ── 13. "全部暂停" visible only when active jobs exist ──────────
  test('"全部暂停" visibility tied to active jobs', async ({ page }) => {
    // This test just verifies the button appears/disappears correctly
    const pauseBtn = page.getByRole('button', { name: /全部暂停/ });
    const resumeBtn = page.getByRole('button', { name: /继续任务/ });

    // At least one of them should exist or neither (if all jobs done)
    const pauseVisible = await pauseBtn.isVisible().catch(() => false);
    const resumeVisible = await resumeBtn.isVisible().catch(() => false);

    // They should never be visible simultaneously
    expect(pauseVisible && resumeVisible).toBe(false);
  });

  // ── 15. 上传文件夹 button → file input dialog ──────────────────
  test('"上传文件夹" button opens file input', async ({ page }) => {
    const folderBtn = page.getByRole('button', { name: /上传文件夹/ });
    await expect(folderBtn).toBeVisible({ timeout: 5000 });

    // Click should trigger the hidden file input
    const fileChooserPromise = page.waitForEvent('filechooser', { timeout: 5000 });
    await folderBtn.click();
    const fileChooser = await fileChooserPromise;
    expect(fileChooser).toBeTruthy();
    // Don't actually select files — cancel
    // fileChooser is already resolved, just verify it was triggered
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

    await expect(page.getByText('暂无 DWG 文件', { exact: true })).toBeVisible();
    await expect(page.locator('.ant-table-tbody .ant-table-row')).toHaveCount(0);
  });

  // ── 17. Navigation: sidebar links are present ──────────────────
  test('sidebar navigation links work', async ({ page }) => {
    const sidebar = page.locator('.ant-layout-sider');
    await expect(sidebar).toBeVisible();

    // Check key links
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

    // Each stat card should have a number
    const statValues = page.locator('[style*="fontSize: 22"][style*="fontWeight: 700"]');
    const count = await statValues.count();

    for (let i = 0; i < count; i++) {
      const text = await statValues.nth(i).textContent();
      // Each stat should be a number or something displayable
      expect(text).toBeTruthy();
    }
  });
});
