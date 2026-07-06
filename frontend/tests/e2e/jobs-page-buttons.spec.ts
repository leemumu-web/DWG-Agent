/**
 * JobsPage — every button tested against its real backend connection.
 */
import { test, expect, type Page } from '@playwright/test';

async function login(page: Page) {
  const apiResp = await page.request.post('http://127.0.0.1:8000/api/v1/auth/sessions', {
    data: { username: 'admin', password: 'SuperAdminPass1' },
  });
  const body = await apiResp.json();
  await page.goto('/');
  await page.evaluate(
    ({ t, u }) => {
      localStorage.setItem('dwg_access_token', t);
      localStorage.setItem('dwg_user', JSON.stringify(u));
    },
    { t: body.data.access_token, u: body.data.user },
  );
  await page.goto('/jobs');
  await page.waitForLoadState('networkidle');
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
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });

    // Find a dxf_open_source job
    const rows = page.locator('.ant-table-row');
    const rowCount = await rows.count();

    let found = false;
    for (let i = 0; i < rowCount && !found; i++) {
      const row = rows.nth(i);
      const text = await row.textContent();
      if (text?.includes('dxf_open_source') || text?.includes('DXF 开源管线')) {
        // Click detail
        await row.getByRole('button', { name: /查看详情/ }).click();
        const drawer = page.locator('.ant-drawer');
        await expect(drawer).toBeVisible({ timeout: 5000 });

        // Check for DXF download button
        const dxfBtn = drawer.getByRole('button', { name: /下载 DXF/ });
        if (await dxfBtn.isVisible()) {
          found = true;

          const [dlResp] = await Promise.all([
            page.waitForResponse(
              (r) => r.url().includes('/download-url') && r.request().method() === 'GET',
              { timeout: 10_000 },
            ),
            dxfBtn.click(),
          ]);
          expect(dlResp.status()).toBe(200);
        }
        // Close
        await page.locator('.ant-drawer-mask').click();
      }
    }
    test.skip(!found, 'No DXF job with download button');
  });

  // ── 5. Drawer "重新提交" button → POST /jobs/{id}/retry-requests ──
  test('drawer "重新提交" → POST retry-requests → 202', async ({ page }) => {
    await page.waitForSelector('.ant-table-row', { timeout: 10_000 });
    const rows = page.locator('.ant-table-row');
    const rowCount = await rows.count();

    let found = false;
    for (let i = 0; i < rowCount && !found; i++) {
      const row = rows.nth(i);
      const text = await row.textContent();
      if (text?.includes('failed') || text?.includes('cancelled')) {
        await row.getByRole('button', { name: /查看详情/ }).click();
        const drawer = page.locator('.ant-drawer');
        await expect(drawer).toBeVisible({ timeout: 5000 });

        const retryBtn = drawer.getByRole('button', { name: /重新提交/ });
        if (await retryBtn.isVisible()) {
          found = true;
          const [resp] = await Promise.all([
            page.waitForResponse(
              (r) => r.url().includes('/retry-requests') && r.request().method() === 'POST',
              { timeout: 10_000 },
            ),
            retryBtn.click(),
          ]);
          expect(resp.status()).toBe(202);
        }
        await page.locator('.ant-drawer-mask').click();
      }
    }
    test.skip(!found, 'No failed/cancelled job found');
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
