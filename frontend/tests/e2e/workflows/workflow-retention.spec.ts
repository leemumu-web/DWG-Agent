import { expect, test, type Route } from '@playwright/test';

const now = '2026-07-26T03:00:00Z';
const envelope = (data: unknown) => ({
  data,
  meta: { request_id: 'retention-browser-e2e', timestamp: now },
});
const user = {
  id: 1,
  username: 'admin',
  real_name: '生产管理员',
  status: 'active',
  roles: [{ id: 1, code: 'super_admin', name: '超级管理员', is_system: true }],
  created_at: now,
  updated_at: now,
};

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(envelope(data)),
  });
}

test('terminal workflow restores, downloads and asynchronously purges complete backup', async ({ page }) => {
  const stage = {
    id: 501,
    stage_code: 'source_intake',
    name: '文件接收与输入冻结',
    sequence: 1,
    status: 'succeeded',
    job_id: null,
    job_attempt: null,
    progress: 100,
    input_json: null,
    output_json: null,
    error_code: null,
    error_message: null,
    started_at: now,
    finished_at: now,
    created_at: now,
    updated_at: now,
  };
  const workflow = {
    id: 51,
    project_id: 9,
    created_by: 1,
    name: '已完成钢构生产批次',
    workflow_type: 'linux_production',
    status: 'succeeded',
    current_stage: null,
    progress: 100,
    config_json: { definition_revision: 4 },
    error_code: null,
    error_message: null,
    started_at: now,
    finished_at: now,
    created_at: now,
    updated_at: now,
    stages: [stage],
    artifacts: [],
  };
  const template = {
    code: 'linux_production',
    name: 'Linux 生产流程',
    description: '服务器端生产编排框架',
    stages: [{
      code: stage.stage_code,
      name: stage.name,
      description: '冻结生产输入。',
      execution_mode: 'manual',
      implementation_status: 'implemented',
      execution_kind: null,
      required_inputs: [],
      artifact_types: [],
      required_outputs: [],
    }],
  };
  const preview = {
    workflow_id: 51,
    workflow_status: 'succeeded',
    terminal: true,
    blocked: false,
    blockers: [],
    file_count: 14,
    preview_cache_count: 3,
    source_size_bytes: 8192,
    reclaimable_size_bytes: 9728,
  };
  let retention: Record<string, unknown> | null = null;
  let purgeStatusReads = 0;
  let submittedConfirmation: unknown;
  let downloadRequested = false;

  const prepared = () => ({
    export_uid: '6ce11618-965c-4fc6-8310-f66cd184dfad',
    workflow_run_id: 51,
    status: 'prepared',
    file_count: 14,
    preview_cache_count: 3,
    source_size_bytes: 8192,
    reclaimable_size_bytes: 9728,
    filename: 'workflow-51-完整备份.zip',
    download_url: '/api/v1/workflows/51/retention-exports/6ce11618-965c-4fc6-8310-f66cd184dfad/download',
    token_expires_at: '2026-07-26T04:00:00Z',
    downloaded_at: null,
    task_id: null,
    purge_started_at: null,
    purged_at: null,
    purged_file_count: 0,
    purged_size_bytes: 0,
    error_code: null,
    error_message: null,
    created_at: now,
    updated_at: now,
  });

  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, {
    access_token: 'e2e-token',
    user,
  }, 201));
  await page.route('**/api/v1/workflows/projects?**', (route) => route.fulfill({
    json: {
      ...envelope([{ id: 9, code: 'DONE', name: '已完成项目' }]),
      pagination: { page: 1, page_size: 200, total: 1, total_pages: 1 },
    },
  }));
  await page.route('**/api/v1/workflows/templates', (route) => json(route, [template]));
  await page.route('**/api/v1/workflows/51/retention-preview', (route) => json(route, preview));
  await page.route('**/api/v1/workflows/51/retention-exports/latest', (route) => json(route, retention));
  await page.route('**/api/v1/workflows/51/retention-exports', async (route) => {
    retention = prepared();
    await json(route, retention, 201);
  });
  await page.route(/\/api\/v1\/workflows\/51\/retention-exports\/[0-9a-f-]{36}\/purge$/, async (route) => {
    submittedConfirmation = route.request().postDataJSON();
    retention = {
      ...retention,
      status: 'purge_queued',
      task_id: 'maintenance-retention-51',
      updated_at: now,
    };
    await json(route, retention, 202);
  });
  await page.route(/\/api\/v1\/workflows\/51\/retention-exports\/[0-9a-f-]{36}$/, async (route) => {
    if (['purge_queued', 'purging'].includes(String(retention?.status))) {
      purgeStatusReads += 1;
      retention = purgeStatusReads < 2
        ? { ...retention, status: 'purging' }
        : {
            ...retention,
            status: 'purged',
            purged_at: now,
            purged_file_count: 17,
            purged_size_bytes: 9728,
            download_url: null,
            updated_at: now,
          };
    }
    await json(route, retention);
  });
  await page.route(/\/api\/v1\/workflows\/51\/retention-exports\/[0-9a-f-]{36}\/download$/, async (route) => {
    downloadRequested = true;
    retention = { ...retention, status: 'downloading', updated_at: now };
    await new Promise((resolve) => setTimeout(resolve, 200));
    await route.fulfill({
      status: 200,
      contentType: 'application/zip',
      headers: {
        'content-disposition': "attachment; filename*=UTF-8''workflow-51-%E5%AE%8C%E6%95%B4%E5%A4%87%E4%BB%BD.zip",
        'content-length': '8192',
      },
      body: Buffer.alloc(8192, 65),
    });
  });
  await page.route(/\/api\/v1\/workflows\/51$/, (route) => json(route, workflow));

  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'e2e-token', savedUser: user });
  await page.goto('/workflows/51');

  await expect(page.getByRole('button', { name: '完整备份与释放空间' })).toBeVisible();
  await page.getByRole('button', { name: '完整备份与释放空间' }).click();
  const dialog = page.getByRole('dialog', { name: /生产批次 #51/ });
  await expect(dialog.getByText('14 个')).toBeVisible();
  await expect(dialog.getByText('3 个')).toBeVisible();
  await dialog.getByRole('button', { name: '生成完整备份' }).click();
  await expect(dialog.getByRole('button', { name: '下载完整备份' })).toBeVisible();

  await dialog.getByRole('button', { name: /关\s*闭/ }).click();
  await page.reload();
  await page.getByRole('button', { name: '完整备份与释放空间' }).click();
  const restored = page.getByRole('dialog', { name: /生产批次 #51/ });
  await expect(restored.getByRole('button', { name: '下载完整备份' })).toBeVisible();

  const downloadPromise = page.waitForEvent('download');
  await restored.getByRole('button', { name: '下载完整备份' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain('workflow-51-完整备份.zip');
  expect(downloadRequested).toBe(true);
  await expect(restored.getByLabel('完整备份下载进度')).toHaveAttribute('aria-valuenow', '100');
  retention = {
    ...retention,
    status: 'downloaded',
    downloaded_at: now,
    updated_at: now,
  };
  await expect(restored.getByText('服务端已确认完整备份发送完毕')).toBeVisible();

  const purgeButton = restored.getByRole('button', { name: '永久删除本批服务器文件' });
  await expect(purgeButton).toBeDisabled();
  await restored.getByRole('checkbox', { name: /我已在本地打开 ZIP/ }).check();
  await restored.getByPlaceholder('DELETE WORKFLOW 51').fill('DELETE WORKFLOW');
  await expect(purgeButton).toBeDisabled();
  await restored.getByPlaceholder('DELETE WORKFLOW 51').fill('DELETE WORKFLOW 51');
  await expect(purgeButton).toBeEnabled();
  await purgeButton.click();

  await expect.poll(() => submittedConfirmation).toEqual({
    confirmation: 'DELETE WORKFLOW 51',
  });
  await expect(restored.getByText('本批服务器文件已永久清理')).toBeVisible({ timeout: 8_000 });
  await expect(restored.getByText(/共清理 17 个对象/)).toBeVisible();
});
