import { expect, test, type Route } from '@playwright/test';

const now = '2026-08-06T08:00:00Z';
const user = {
  id: 1,
  username: 'admin',
  real_name: '生产管理员',
  status: 'active',
  password_reset_required: false,
  roles: [{ id: 1, code: 'super_admin', name: '系统管理员', is_system: true }],
  created_at: now,
  updated_at: now,
};

function envelope(data: unknown) {
  return { data, meta: { request_id: 'branding-e2e', timestamp: now } };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

test('login uses the supplied joint brand logo', async ({ page }) => {
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, {
    error: { code: 'UNAUTHENTICATED', message: '未登录' },
  }, 401));

  await page.goto('/login');

  const logo = page.getByRole('img', { name: '中国五矿、中冶集团与宝冶钢构' });
  await expect(logo).toBeVisible();
  await expect(logo).toHaveAttribute('src', '/brand/logo-on-blue.png');
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute('href', '/brand/logo-on-light.png');

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileLogo = page.getByRole('img', { name: '中国五矿、中冶集团与宝冶钢构' });
  await expect(mobileLogo).toBeVisible();
  await expect(mobileLogo).toHaveAttribute('src', '/brand/logo-on-light.png');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
});

test('main navigation uses the compact supplied logo', async ({ page }) => {
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, envelope({
    access_token: 'branding-token',
    user,
  }), 201));

  await page.goto('/profile');

  const logo = page.getByRole('img', { name: '中国五矿、中冶集团与宝冶钢构' });
  await expect(logo).toBeVisible();
  await expect(logo).toHaveAttribute('src', '/brand/logo-on-dark.png');
  await expect(page.locator('.app-brand-mark')).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '打开导航' }).click();
  const drawerLogo = page.locator('.ant-drawer .app-brand-logo img');
  await expect(drawerLogo).toBeVisible();
  await expect(drawerLogo).toHaveAttribute('src', '/brand/logo-on-dark.png');
});
