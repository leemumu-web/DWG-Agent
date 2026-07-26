import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-26T12:00:00Z';
const user = {
  id: 1,
  username: 'admin',
  real_name: '系统管理员',
  status: 'active',
  roles: [{ id: 1, code: 'super_admin', name: '系统管理员', is_system: true }],
  created_at: now,
  updated_at: now,
};

function envelope(data: unknown) {
  return { data, meta: { request_id: 'progress-e2e', timestamp: now } };
}

function pageEnvelope(data: unknown[]) {
  return {
    ...envelope(data),
    pagination: { page: 1, page_size: 200, total: data.length, total_pages: 1 },
  };
}

async function json(route: Route, payload: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });
}

async function installProgressState(page: Page) {
  const files = [1, 2, 3, 4].map((index) => ({
    id: 95_000 + index,
    bucket: 'factory-source',
    storage_key: `uploads/storage-${index}.dwg`,
    original_name: `生产图纸-${index}.dwg`,
    file_ext: '.dwg',
    content_type: 'application/acad',
    size_bytes: 2048,
    sha256: String(index).repeat(64),
    batch_name: 'progress-fixture',
    status: 'available',
    created_at: now,
    updated_at: now,
  }));
  const jobs = [
    {
      id: 96_001,
      status: 'succeeded',
      progress: 100,
      fileId: files[0].id,
      progressData: null,
    },
    {
      id: 96_002,
      status: 'running',
      progress: 20,
      fileId: files[1].id,
      progressData: {
        phase: 'oda_converting',
        phase_label: 'ODA 转换中',
        message: '源 DWG 已就绪，ODA 正在转换',
        indeterminate: true,
        progress_basis: 'confirmed_milestone',
      },
    },
    {
      id: 96_003,
      status: 'failed',
      progress: 70,
      fileId: files[2].id,
      progressData: null,
    },
  ].map((job) => ({
    id: job.id,
    task_type: 'convert_dwg_to_dxf',
    precision_level: 'normal',
    pipeline: 'dxf_open_source',
    status: job.status,
    attempt: 1,
    priority: 0,
    progress: job.progress,
    params_json: { file_id: job.fileId, batch_name: 'progress-fixture' },
    error_code: job.status === 'failed' ? 'DXF_CONVERSION_FAILED' : null,
    error_message: job.status === 'failed' ? '该图纸转换未完成，请重新提交。' : null,
    progress_data: job.progressData,
    result_available: job.status === 'succeeded' ? true : null,
    created_at: now,
    updated_at: now,
    started_at: now,
    finished_at: job.status === 'running' ? null : now,
  }));

  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(
    route,
    envelope({ access_token: 'progress-token', user }),
    201,
  ));
  await page.route('**/api/v1/files/batches?**', (route) => json(route, envelope([])));
  await page.route('**/api/v1/files?**', (route) => json(route, pageEnvelope(files)));
  await page.route('**/api/v1/workflows/jobs?**', (route) => json(route, pageEnvelope(jobs)));
  await page.route('**/api/v1/workflows/jobs/events/stream?**', (route) => route.fulfill({
    status: 200,
    contentType: 'text/event-stream',
    body: `data: ${JSON.stringify({ type: 'snapshot', jobs: [] })}\n\n`,
  }));
}

test('conversion batch progress uses terminal file count and shows the confirmed ODA phase', async ({ page }) => {
  await installProgressState(page);
  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'progress-token', savedUser: user });
  await page.goto('/files/dwg2dxf');

  await expect(page.getByText(/处理完成度/)).toBeVisible();
  await expect(page.getByText(/成功 1.*失败 1.*处理中 1.*待提交\/重试 2.*50%/)).toBeVisible();
  await expect(page.getByText('ODA 转换中')).toBeVisible();
  const aggregate = page.locator('.conversion-progress').getByRole('progressbar');
  await expect(aggregate).toHaveAttribute('aria-valuenow', '50');
});
