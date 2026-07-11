import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { expect, test, type Page } from '@playwright/test';

import { API_BASE } from './test-env';

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
    const jobId = (await submitResponse.json()).data.job_id as number;

    const first = await waitForJob(page, token, jobId, 1);
    expect(first.status).toBe('failed');
    expect(first.error_message).toContain('流水线处理失败');
    expect(first.error_message.length).toBeLessThan(300);
    expect(first.error_message).not.toMatch(/Traceback|\/home\/|pandas\.errors/);

    const row = page.locator(`.ant-table-row[data-row-key="${jobId}"]`);
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect(row).toContainText('failed', { timeout: 10_000 });
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
