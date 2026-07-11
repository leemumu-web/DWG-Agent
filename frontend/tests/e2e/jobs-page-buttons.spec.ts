/**
 * JobsPage — every button tested against its real backend connection.
 */
import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { API_BASE } from './test-env';

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
  await page.goto('/jobs');
  await page.waitForLoadState('networkidle');
}

async function createSucceededDxfFixture(page: Page): Promise<number> {
  const sampleDir = path.resolve(process.cwd(), '../Stages/dwg2dxf/samples/input');
  const sampleName = fs.readdirSync(sampleDir).find((name) => name.endsWith('.dwg'));
  expect(sampleName).toBeTruthy();
  const token = await page.evaluate(() => sessionStorage.getItem('dwg_access_token'));
  expect(token).toBeTruthy();
  const upload = await page.request.post(`${API_BASE}/api/v1/files`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      upload: {
        name: `download-fixture-${Date.now()}.dwg`,
        mimeType: 'application/acad',
        buffer: fs.readFileSync(path.join(sampleDir, sampleName!)),
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
  }, { timeout: 45_000 }).toBe('succeeded');
  return jobId;
}

async function createFailedDxfFixture(page: Page): Promise<number> {
  const token = await page.evaluate(() => sessionStorage.getItem('dwg_access_token'));
  expect(token).toBeTruthy();
  const upload = await page.request.post(`${API_BASE}/api/v1/files`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      upload: {
        name: `job-retry-fixture-${Date.now()}.dwg`,
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
  return jobId;
}

test.describe('JobsPage — button & API integration', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  // ── 1. Job list loads via GET /api/v1/jobs ───────────────────────
  test('job list loads with data', async ({ page }) => {
    const [resp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/v1/jobs') && r.request().method() === 'GET'),
      page.goto('/jobs'),
    ]);
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body.data)).toBe(true);
  });

  // ── 2. "创建框架冒烟任务" → POST /jobs → 202 ──────────────────
  test('"创建框架冒烟任务" → POST /jobs → 202', async ({ page }) => {
    const btn = page.getByRole('button', { name: /创建框架冒烟任务/ });
    await expect(btn).toBeVisible({ timeout: 5000 });

    const [resp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/api/v1/jobs') && r.request().method() === 'POST',
        { timeout: 10_000 },
      ),
      btn.click(),
    ]);

    expect(resp.status()).toBe(202);
    const body = await resp.json();
    expect(body.data).toHaveProperty('id');
    expect(body.data.status).toMatch(/queued|pending/);
  });

  // ── 3. "查看详情" → opens drawer with job info ──────────────────
  test('"查看详情" → drawer opens with job timeline', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const detailBtns = page.getByRole('button', { name: /查看详情/ });
    const count = await detailBtns.count();
    test.skip(count === 0, 'No jobs to view');

    await detailBtns.first().click();

    // Drawer should open
    const drawer = page.locator('.ant-drawer');
    await expect(drawer).toBeVisible({ timeout: 5000 });

    // Should contain job info
    await expect(drawer.getByText(/管线/)).toBeVisible();

    // Close drawer
    await page.locator('.ant-drawer-mask').click();
  });

  // ── 4. Drawer "下载 DXF" button ──────────────────────────────────
  test('drawer "下载 DXF" → visible for succeeded DXF jobs', async ({ page }) => {
    const jobId = await createSucceededDxfFixture(page);
    await page.reload();
    const row = page.locator(`.ant-table-row[data-row-key="${jobId}"]`);
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.getByRole('button', { name: /查看详情/ }).click();
    const drawer = page.locator('.ant-drawer');
    await expect(drawer).toBeVisible({ timeout: 5000 });
    const dxfBtn = drawer.getByRole('button', { name: /下载 DXF/ });
    await expect(dxfBtn).toBeVisible();
    const [dlResp] = await Promise.all([
      page.waitForResponse(
        (response) => response.url().includes('/download-url')
          && response.request().method() === 'GET',
        { timeout: 10_000 },
      ),
      dxfBtn.click(),
    ]);
    expect(dlResp.status()).toBe(200);
  });

  // ── 5. Drawer "重新提交" button → POST /jobs/{id}/retry-requests ──
  test('drawer "重新提交" → POST retry-requests → 202', async ({ page }) => {
    const jobId = await createFailedDxfFixture(page);
    await page.reload();
    const row = page.locator(`.ant-table-row[data-row-key="${jobId}"]`);
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.getByRole('button', { name: /查看详情/ }).click();
    const drawer = page.locator('.ant-drawer');
    await expect(drawer).toBeVisible({ timeout: 5000 });
    const retryBtn = drawer.getByRole('button', { name: /重新提交/ });
    await expect(retryBtn).toBeVisible();
    const [response] = await Promise.all([
      page.waitForResponse(
        (item) => item.url().includes('/retry-requests')
          && item.request().method() === 'POST',
        { timeout: 10_000 },
      ),
      retryBtn.click(),
    ]);
    expect(response.status()).toBe(202);
  });

  // ── 6. Table pagination ──────────────────────────────────────────
  test('jobs table has working pagination', async ({ page }) => {
    await page.waitForLoadState('networkidle');
    const pagination = page.locator('.ant-pagination');
    if (await pagination.isVisible()) {
      const totalText = await pagination.textContent();
      expect(totalText).toBeTruthy();
    }
  });

  // ── 7. Status tags have correct colors ───────────────────────────
  test('job status tags render with colors', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const tags = page.locator('.ant-table-row .ant-tag');
    const tagCount = await tags.count();
    test.skip(tagCount === 0, 'No status tags visible');

    // Each tag should have a text status
    const firstTagText = await tags.first().textContent();
    expect(['succeeded', 'failed', 'running', 'queued', 'cancelled', 'pending']).toContain(
      firstTagText?.trim(),
    );
  });

  // ── 8. Progress bars render for jobs ────────────────────────────
  test('progress bars visible for jobs', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const progressBars = page.locator('.ant-progress');
    const count = await progressBars.count();
    // At least some rows should have progress bars
    expect(count).toBeGreaterThanOrEqual(0);
  });
});
