import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-20T01:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'daily-archive-e2e', timestamp: now } });
const pageEnvelope = (data: unknown[]) => ({ ...envelope(data), pagination: { page: 1, page_size: 10, total: data.length } });
const user = { id: 1, username: 'admin', real_name: '系统管理员', status: 'active', roles: [{ id: 1, code: 'super_admin', name: '系统管理员', is_system: true }], created_at: now, updated_at: now };

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(envelope(data)) });
}

async function openArchiveConsole(page: Page) {
  const overview = { status: 'ok', environment: { app_env: 'production', database_engine: 'mysql', database: 'dwg_agent', storage_backend: 'minio' }, catalog: { available_files: 12, deleted_files: 0, tracked_bytes: 8192 }, transfers_today: { inbound_succeeded: 4, outbound_succeeded: 1, attention_required: 0 }, latest_scan: null };
  const preview = {
    archive_date: '2026-07-20', timezone: 'Asia/Shanghai', scope_bucket: null,
    window_start: '2026-07-19T16:00:00Z', window_end: '2026-07-20T16:00:00Z',
    file_count: 3, total_bytes: 7340032, excluded_archive_files: 1,
    bucket_counts: { 'dwg-original': 2, 'dxf-derived': 1 }, format_counts: { '.dwg': 2, '.dxf': 1 },
    source_manifest_sha256: 'a'.repeat(64), can_archive: true, block_reason: null,
    expires_at: '2099-07-20T01:10:00Z', preview_token: 'signed.preview',
  };
  const queued = {
    id: 41, archive_date: '2026-07-20', timezone: 'Asia/Shanghai', scope_bucket: null,
    status: 'queued', actor_user_id: 1, source_manifest_sha256: 'a'.repeat(64),
    file_count: 3, total_bytes: 7340032, bucket_counts: preview.bucket_counts,
    format_counts: preview.format_counts, task_id: 'daily-task-41', archive_file_id: null,
    manifest_file_id: null, error_code: null, error_message: null, started_at: null,
    finished_at: null, created_at: now, updated_at: now, reused: false,
  };
  const succeeded = { ...queued, status: 'succeeded', archive_file_id: 901, manifest_file_id: 902, started_at: now, finished_at: '2026-07-20T01:00:03Z' };
  let detailCalls = 0;

  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, { access_token: 'archive-token', user }, 201));
  await page.route('**/api/v1/data-admin/overview', (route) => json(route, overview));
  await page.route('**/api/v1/data-admin/daily-archives/preview', (route) => json(route, preview));
  await page.route('**/api/v1/data-admin/daily-archives?**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(pageEnvelope(detailCalls ? [succeeded] : [])) }));
  await page.route('**/api/v1/data-admin/daily-archives/41', (route) => {
    detailCalls += 1;
    return json(route, detailCalls > 1 ? succeeded : queued);
  });
  await page.route('**/api/v1/data-admin/daily-archives', async (route) => {
    if (route.request().method() === 'POST') return json(route, queued, 202);
    return route.fallback();
  });
  await page.route('**/api/v1/files/901/download-url', (route) => json(route, { url: '/api/v1/files/901/download?signature=test', expires_in: 60 }));
  await page.route('**/api/v1/files/901/download?signature=test', (route) => route.fulfill({ status: 200, contentType: 'application/zip', body: 'archive-bytes', headers: { 'Content-Disposition': 'attachment; filename="daily-archive-2026-07-20.zip"' } }));

  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'archive-token', savedUser: user });
  await page.goto('/admin/infrastructure?tab=daily-archive');
}

test('daily archive preview, confirmation, polling and download form one safe workflow', async ({ page }) => {
  await openArchiveConsole(page);
  await expect(page.getByText('非破坏式每日整理')).toBeVisible();
  await expect(page.getByText(/不会移动、重命名、软删除源文件/)).toBeVisible();

  await page.getByRole('button', { name: '预检归档范围' }).click();
  await expect(page.getByText('冻结文件')).toBeVisible();
  await expect(page.getByText('7.0 MiB')).toBeVisible();
  await expect(page.getByText('dwg-original · 2')).toBeVisible();
  await expect(page.getByText('.dxf · 1')).toBeVisible();
  await expect(page.getByText('只新增，不改源文件')).toBeVisible();

  await page.getByRole('button', { name: '确认并生成每日归档' }).click();
  await expect(page.getByText('确认归档 3 个文件？')).toBeVisible();
  await page.getByRole('button', { name: '提交归档' }).click();
  await expect(page.getByText('归档任务 #41', { exact: true })).toBeVisible();
  await expect(page.getByText('归档包和清单均已登记，可安全下载。')).toBeVisible({ timeout: 8_000 });

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 ZIP' }).click();
  await expect((await download).suggestedFilename()).toBe('daily-archive-2026-07-20.zip');
  await expect(page.getByText('下载已开始')).toBeVisible();
});
