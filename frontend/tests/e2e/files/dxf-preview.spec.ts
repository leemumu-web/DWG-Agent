import { expect, test, type Page } from '@playwright/test';

const now = '2026-07-23T10:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'dxf-preview-e2e', timestamp: now } });
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="900" viewBox="0 0 1200 900"><path d="M40 40 L1160 860" stroke="#334155"/></svg>';

async function mockPreviewPage(page: Page, fileName: string, onPreview: () => void) {
  const admin = {
    id: 1, username: 'admin', real_name: '管理员', status: 'active',
    roles: [{ id: 1, code: 'admin', name: '系统管理员', is_system: true }],
    created_at: now, updated_at: now,
  };
  const file = {
    id: 987, bucket: 'playwright', storage_key: `playwright/${fileName}`,
    original_name: fileName, file_ext: '.dxf', content_type: 'application/dxf',
    size_bytes: 2048, sha256: 'a'.repeat(64), batch_name: null,
    uploaded_by: 1, status: 'available', created_at: now, updated_at: now,
  };
  const pageEnvelope = (data: unknown[]) => ({
    ...envelope(data),
    pagination: { page: 1, page_size: 200, total: data.length, total_pages: 1 },
  });

  await page.route('**/api/v1/auth/tokens/refresh', (route) => route.fulfill({
    status: 201, contentType: 'application/json',
    body: JSON.stringify(envelope({ access_token: 'preview-token', user: admin })),
  }));
  await page.route('**/api/v1/files?**', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(pageEnvelope([file])),
  }));
  await page.route('**/api/v1/files/batches?**', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(envelope([])),
  }));
  await page.route('**/api/v1/jobs?**', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(pageEnvelope([])),
  }));
  await page.route('**/api/v1/files/987/dxf-preview/content', (route) => route.fulfill({
    status: 200, contentType: 'image/svg+xml', body: svg,
  }));
  await page.route('**/api/v1/files/987/dxf-preview', (route) => {
    onPreview();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(envelope({
        file_id: 987, file_name: fileName, preview_file_id: 988,
        content_url: '/api/v1/files/987/dxf-preview/content', content_type: 'image/svg+xml',
        document_entities: 3, modelspace_entities: 2,
        entity_counts: { LINE: 2, TEXT: 1 }, layers: ['0'], layer_colors: { 0: 7 },
        bounds: { min_x: 0, min_y: 0, max_x: 1200, max_y: 900 }, cached: true,
      })),
    });
  });
}

test('DXF online preview uses a full-width light canvas without telemetry and supports unbounded panning', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  const fileName = `preview-e2e-${Date.now()}.dxf`;
  let previewRequests = 0;
  await mockPreviewPage(page, fileName, () => { previewRequests += 1; });

  await page.goto('/files/dxf2dwg');
  const row = page.getByRole('row').filter({ hasText: fileName });
  await expect(row).toBeVisible({ timeout: 15_000 });

  await row.getByRole('button', { name: '预览 DXF' }).click();
  await expect.poll(() => previewRequests).toBe(1);

  const dialog = page.getByRole('dialog', { name: /DXF 在线预览/ });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(fileName, { exact: true })).toBeVisible();
  await expect(dialog.getByLabel('DXF 图形信息')).toHaveCount(0);
  await expect(dialog.getByText('Drawing telemetry')).toHaveCount(0);
  await expect(dialog.getByText('SVG / AUTHENTICATED')).toHaveCount(0);
  await expect(dialog.locator('img[alt^="DXF 预览"]')).toHaveAttribute('src', /^blob:/);

  const shell = dialog.locator('.dxf-preview-shell');
  const stage = dialog.locator('.dxf-preview-stage');
  await expect(stage).toHaveCSS('background-color', 'rgb(244, 247, 251)');
  const shellBox = await shell.boundingBox();
  const stageBox = await stage.boundingBox();
  expect(shellBox).not.toBeNull();
  expect(stageBox).not.toBeNull();
  expect(stageBox!.width).toBeGreaterThan(shellBox!.width - 2);

  const transformed = dialog.locator('.react-transform-component');
  const position = async () => transformed.evaluate((element) => {
    const matrix = new DOMMatrixReadOnly(getComputedStyle(element).transform);
    return { x: matrix.m41, y: matrix.m42, scale: matrix.a };
  });
  await expect.poll(async () => (await position()).scale).toBeLessThanOrEqual(0.96);
  const fitted = await position();
  await dialog.getByRole('button', { name: '放大预览' }).click();
  await expect.poll(async () => (await position()).scale).toBeGreaterThan(fitted.scale + 0.1);
  const beforeDrag = await position();
  const centerX = stageBox!.x + stageBox!.width / 2;
  const centerY = stageBox!.y + stageBox!.height / 2;
  await page.mouse.move(centerX, centerY);
  await page.mouse.down();
  await page.mouse.move(centerX + 260, centerY + 180, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => {
    const current = await position();
    return Math.abs(current.x - beforeDrag.x) + Math.abs(current.y - beforeDrag.y);
  }).toBeGreaterThan(250);

  await dialog.getByRole('button', { name: '适合窗口' }).click();
  await expect.poll(async () => (await position()).scale).toBeLessThanOrEqual(1);
  await dialog.getByRole('button', { name: '缩小预览' }).click();
  await expect(dialog.getByRole('button', { name: '重新加载' })).toBeVisible();
  await expect(dialog.getByRole('button', { name: '下载源文件' })).toBeVisible();
  await dialog.getByRole('button', { name: '重新加载' }).click();
  await expect.poll(() => previewRequests).toBe(2);
  await dialog.getByRole('button', { name: '关闭预览' }).click();
  await expect(dialog).toBeHidden();
  expect(errors).toEqual([]);
});
