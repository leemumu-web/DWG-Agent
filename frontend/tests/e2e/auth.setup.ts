/**
 * Auth helper: login via API and store token in localStorage so the
 * Playwright browser starts authenticated (avoids login-page tests in
 * every spec).
 */
import { test as setup } from '@playwright/test';

const AUTH_FILE = 'tests/e2e/.auth.json';

setup('authenticate', async ({ request, page }) => {
  // Login via API
  const resp = await request.post('http://127.0.0.1:8000/api/v1/auth/sessions', {
    data: { username: 'admin', password: 'SuperAdminPass1' },
  });
  if (!resp.ok()) throw new Error(`Login failed: ${resp.status()} ${await resp.text()}`);
  const body = await resp.json();
  const token = body.data.access_token;
  const user = body.data.user;

  // Prime localStorage so the app starts logged in
  await page.goto('/');
  await page.evaluate(
    ({ t, u }) => {
      localStorage.setItem('dwg_access_token', t);
      localStorage.setItem('dwg_user', JSON.stringify(u));
    },
    { t: token, u: user },
  );

  await page.context().storageState({ path: AUTH_FILE });
});
