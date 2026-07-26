import { expect, test, type Page, type Route } from '@playwright/test';
import path from 'node:path';

const now = '2026-07-26T08:00:00Z';
const user = {
  id: 1,
  username: 'admin',
  real_name: '生产管理员',
  status: 'active',
  roles: [{ id: 1, code: 'super_admin', name: '系统管理员', is_system: true }],
  created_at: now,
  updated_at: now,
};

function envelope(data: unknown) {
  return { data, meta: { request_id: 'dashboard-e2e', timestamp: now } };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function mockProductionApis(page: Page) {
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, envelope({
    access_token: 'dashboard-token',
    user,
  }), 201));
  await page.route('**/api/v1/workflows/templates', (route) => json(route, envelope([{
    code: 'linux_production',
    name: '生产流程',
    description: '从资料入库到 Excel 整理',
    stages: [
      { code: 'source_intake', name: '资料入库' },
      { code: 'dxf_classification', name: '图纸分类' },
      { code: 'drawing_processing', name: '整批拆板' },
      { code: 'excel_stage1', name: 'Excel 整理' },
    ],
  }])));
  await page.route('**/api/v1/workflows?**', (route) => json(route, {
    ...envelope([{
      id: 31,
      project_id: 7,
      project_code: 'P-2026-007',
      project_name: '一号厂房',
      created_by: 1,
      name: '一号厂房生产流程',
      workflow_type: 'linux_production',
      status: 'waiting_input',
      current_stage: 'dxf_classification',
      progress: 25,
      created_at: now,
      updated_at: now,
    }]),
    pagination: { page: 1, page_size: 20, total: 1 },
    summary: { total: 1, running: 0, waiting: 1, completed: 0 },
  }));
}

test('production workbench is backed by workflows and opens the real project form', async ({ page }) => {
  const requested: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/v1/')) requested.push(new URL(request.url()).pathname);
  });
  await mockProductionApis(page);
  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'dashboard-token', savedUser: user });
  await page.goto('/dashboard');

  await expect(page.getByRole('heading', { name: /这里是生产工作台/ })).toBeVisible();
  await expect(page.locator('.dashboard-production-hero__copy > .ant-typography').last()).toHaveCSS('color', 'rgb(213, 232, 236)');
  await expect(page.getByText('P-2026-007')).toBeVisible();
  await expect(page.getByText('图纸分类', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('生产操作手册')).toBeVisible();
  await expect(page.getByText('先看这里：一批资料怎样走完')).toBeVisible();
  await expect(page.getByRole('tab', { name: /1\. 建立项目与准备资料/ })).toBeVisible();
  await page.getByRole('tab', { name: /2\. 资料上传与入库冻结/ }).click();
  await expect(page.getByText('一个项目只上传一份 Tekla 原始表')).toBeVisible();
  await expect(page.getByText(/超过 1000 个文件时，只接收浏览器列出的前 1000 个/)).toBeVisible();
  await page.getByRole('tab', { name: /3\. 图纸分类与数量核对/ }).click();
  await expect(page.getByText(/原始 DWG 只负责留档/)).toBeVisible();
  await page.getByRole('tab', { name: /4\. BH、BOX 整批拆板/ }).click();
  await expect(page.getByText(/原长版和余量增长版/)).toBeVisible();
  await page.getByRole('tab', { name: /5\. Excel 整理与重量核验/ }).click();
  await expect(page.getByText(/板材统一按 7\.85/)).toBeVisible();
  await expect(page.getByText(/BH、BOX、BT 拆板后的腹板与翼板重量要合并/)).toBeVisible();
  await page.getByRole('tab', { name: /6\. 下载交付与异常处理/ }).click();
  await expect(page.getByText(/每完成一个阶段只会解锁下一阶段/)).toBeVisible();
  await expect(page.getByText('Stage 1')).toHaveCount(0);
  await expect(page.getByText('本机开发版')).toHaveCount(0);

  await page.getByRole('tab', { name: /1\. 建立项目与准备资料/ }).click();
  await expect(page.getByText('准备什么', { exact: true })).toBeVisible();
  await expect(page.getByText('计算与查询规则', { exact: true })).toBeHidden();
  await page.screenshot({
    path: path.resolve(process.cwd(), '../output/playwright/production-workbench-final.png'),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByText('准备什么', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({
    path: path.resolve(process.cwd(), '../output/playwright/production-workbench-mobile.png'),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 720 });

  await page.getByRole('button', { name: /新建生产项目/ }).click();
  await expect(page).toHaveURL(/\/workflows\?new=1$/);
  await expect(page.getByRole('dialog', { name: '新建生产项目' })).toBeVisible();
  await expect(page.getByLabel('项目编号')).toBeVisible();
  await expect(page.getByLabel('项目名称')).toBeVisible();

  expect(new Set(requested)).toEqual(new Set([
    '/api/v1/auth/tokens/refresh',
    '/api/v1/workflows',
    '/api/v1/workflows/templates',
  ]));
});
