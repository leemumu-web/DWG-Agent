import { expect, test, type Page } from '@playwright/test';

const now = '2026-07-23T10:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'dxf-preview-e2e', timestamp: now } });
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80" viewBox="0 0 240 80"><path d="M8 8 L232 72" stroke="#334155"/></svg>';

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
        bounds: { min_x: 0, min_y: 0, max_x: 240, max_y: 80 }, cached: true,
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
  const image = dialog.locator('img[alt^="DXF 预览"]');
  await expect(image).toHaveAttribute('src', /^blob:/);
  await expect.poll(async () => {
    const box = await image.boundingBox();
    return box ? box.width / box.height : 0;
  }).toBeCloseTo(3, 1);

  const shell = dialog.locator('.dxf-preview-shell');
  const stage = dialog.locator('.dxf-preview-stage');
  await expect(stage).toHaveCSS('background-color', 'rgb(244, 247, 251)');
  await expect.poll(async () => {
    const [shellBox, stageBox] = await Promise.all([
      shell.boundingBox(),
      stage.boundingBox(),
    ]);
    if (!shellBox || !stageBox || shellBox.width === 0) return 0;
    return stageBox.width / shellBox.width;
  }).toBeGreaterThan(0.99);

  const transformed = dialog.locator('.react-transform-component');
  const position = async () => transformed.evaluate((element) => {
    const matrix = new DOMMatrixReadOnly(getComputedStyle(element).transform);
    return { x: matrix.m41, y: matrix.m42, scale: matrix.a };
  });
  const fitMetrics = async () => {
    const currentStage = await stage.boundingBox();
    const currentImage = await image.boundingBox();
    const current = await position();
    if (!currentStage || !currentImage) {
      return {
        scaleError: Number.POSITIVE_INFINITY,
        xCenterRatio: Number.POSITIVE_INFINITY,
        yCenterRatio: Number.POSITIVE_INFINITY,
      };
    }
    const expectedScale = Math.min(currentStage.width / 240, currentStage.height / 80) * 0.96;
    const xCenterError = Math.abs(
      currentImage.x + currentImage.width / 2 - (currentStage.x + currentStage.width / 2),
    );
    const yCenterError = Math.abs(
      currentImage.y + currentImage.height / 2 - (currentStage.y + currentStage.height / 2),
    );
    return {
      scaleError: Math.abs(current.scale - expectedScale),
      xCenterRatio: xCenterError / currentStage.width,
      yCenterRatio: yCenterError / currentStage.height,
    };
  };
  const expectFitted = async () => {
    await expect.poll(async () => (await fitMetrics()).scaleError).toBeLessThan(0.01);
    await expect.poll(async () => (await fitMetrics()).xCenterRatio).toBeLessThan(0.015);
    await expect.poll(async () => (await fitMetrics()).yCenterRatio).toBeLessThan(0.015);
  };
  await expectFitted();

  await page.setViewportSize({ width: 900, height: 800 });
  await expectFitted();
  const fitted = await position();
  await dialog.getByRole('button', { name: '放大预览' }).click();
  await expect.poll(async () => (await position()).scale).toBeGreaterThan(fitted.scale + 0.1);
  const beforeDrag = await position();
  const resizedStageBox = await stage.boundingBox();
  expect(resizedStageBox).not.toBeNull();
  const centerX = resizedStageBox!.x + resizedStageBox!.width / 2;
  const centerY = resizedStageBox!.y + resizedStageBox!.height / 2;
  await page.mouse.move(centerX, centerY);
  await page.mouse.down();
  await page.mouse.move(centerX + 260, centerY + 180, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => {
    const current = await position();
    return Math.abs(current.x - beforeDrag.x) + Math.abs(current.y - beforeDrag.y);
  }).toBeGreaterThan(250);

  await dialog.getByRole('button', { name: '适合窗口' }).click();
  await expectFitted();
  await dialog.getByRole('button', { name: '放大预览' }).click();
  await page.mouse.move(centerX, centerY);
  await page.mouse.down();
  await page.mouse.move(centerX - 180, centerY - 120, { steps: 6 });
  await page.mouse.up();
  await stage.dblclick({ position: { x: 80, y: 120 } });
  await expectFitted();
  await dialog.getByRole('button', { name: '缩小预览' }).click();
  await expect(dialog.getByRole('button', { name: '重新加载' })).toBeVisible();
  await expect(dialog.getByRole('button', { name: '下载源文件' })).toBeVisible();
  await dialog.getByRole('button', { name: '重新加载' }).click();
  await expect.poll(() => previewRequests).toBe(2);
  await dialog.getByRole('button', { name: '关闭预览' }).click();
  await expect(dialog).toBeHidden();
  expect(errors).toEqual([]);
});
