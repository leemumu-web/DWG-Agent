import { expect, test, type Page } from '@playwright/test';

import { API_BASE } from './test-env';

const MINIMAL_DXF = `0
SECTION
2
HEADER
9
$ACADVER
1
AC1009
0
ENDSEC
0
SECTION
2
ENTITIES
0
LINE
8
0
10
0.0
20
0.0
30
0.0
11
100.0
21
100.0
31
0.0
0
ENDSEC
0
EOF
`;

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
  return body.data.access_token as string;
}

test('DXF source opens an authenticated SVG preview with working controls', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  const token = await login(page);
  const fileName = `preview-e2e-${Date.now()}.dxf`;
  const upload = await page.request.post(`${API_BASE}/api/v1/files`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      upload: {
        name: fileName,
        mimeType: 'application/dxf',
        buffer: Buffer.from(MINIMAL_DXF),
      },
    },
  });
  expect(upload.status()).toBe(201);

  await page.goto('/files/dxf2dwg');
  const row = page.getByRole('row').filter({ hasText: fileName });
  await expect(row).toBeVisible({ timeout: 15_000 });

  const metadataResponse = page.waitForResponse(
    (response) => response.url().endsWith('/dxf-preview') && response.status() === 200,
  );
  const contentResponse = page.waitForResponse(
    (response) => response.url().includes('/dxf-preview/content') && response.status() === 200,
  );
  await row.getByRole('button', { name: '预览 DXF' }).click();
  expect((await metadataResponse).status()).toBe(200);
  expect((await contentResponse).status()).toBe(200);

  const dialog = page.getByRole('dialog', { name: /DXF 在线预览/ });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(fileName, { exact: true })).toBeVisible();
  await expect(dialog.getByText('文档实体')).toBeVisible();
  await expect(dialog.getByText('模型空间')).toBeVisible();
  await expect(dialog.locator('img[alt^="DXF 预览"]')).toHaveAttribute('src', /^blob:/);

  await dialog.getByRole('button', { name: '放大预览' }).click();
  await dialog.getByRole('button', { name: '缩小预览' }).click();
  await dialog.getByRole('button', { name: '重置预览' }).click();
  await dialog.getByRole('button', { name: '关闭预览' }).click();
  await expect(dialog).toBeHidden();
  expect(errors).toEqual([]);
});
