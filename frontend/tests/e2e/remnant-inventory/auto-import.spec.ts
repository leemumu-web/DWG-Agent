import { expect, test, type Route } from '@playwright/test';

const now = '2026-07-24T08:00:00Z';
const envelope = (data: unknown) => ({
  data,
  meta: { request_id: 'remnant-auto-import-e2e', timestamp: now },
});
const worker = {
  id: 7,
  username: 'remnant-worker',
  real_name: '余料工人',
  status: 'active',
  roles: [{ id: 7, code: 'remnant_worker', name: '余料工人', is_system: true }],
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

const autoBatch = {
  id: 88,
  created_by: worker.id,
  import_mode: 'auto',
  default_project_no: '项目甲',
  source_folder_name: '项目甲',
  status: 'awaiting_confirmation',
  total_count: 2,
  converting_count: 0,
  parsing_count: 0,
  pending_count: 1,
  confirmed_count: 0,
  failed_count: 1,
  cancelled_count: 0,
  created_at: now,
  updated_at: now,
  items: [
    {
      id: 801,
      batch_id: 88,
      source_file_id: 1801,
      dxf_file_id: 2801,
      original_name: '一号板.dxf',
      source_ext: '.dxf',
      source_relative_path: '一层/一号板.dxf',
      attempt: 1,
      status: 'pending_confirmation',
      material_candidates: [],
      project_candidates: [],
      part_candidates: [],
      warnings: [],
      standard_parse: {
        block_type: 'offcut_zh_cn',
        raw_specification: '-12.5 × 1000 X 2000',
        thickness: '12.5',
        length: '1000',
        width: '2000',
        material: 'Q355B',
        remnant_number: 'YL-001',
      },
      thickness_mm: '12.5',
      material_id: 1,
      project_no: '项目甲',
      parts: ['YL-001'],
      error_code: null,
      error_message: null,
    },
    {
      id: 802,
      batch_id: 88,
      source_file_id: 1802,
      dxf_file_id: null,
      original_name: '坏图.dwg',
      source_ext: '.dwg',
      source_relative_path: '二层/坏图.dwg',
      attempt: 1,
      status: 'failed',
      material_candidates: [],
      project_candidates: [],
      part_candidates: [],
      warnings: [],
      standard_parse: null,
      thickness_mm: null,
      material_id: null,
      project_no: '项目甲',
      parts: [],
      error_code: 'REMNANT_PARSE_FAILED',
      error_message: null,
    },
  ],
} as const;

test('worker sees automatic import and material management without losing batch import', async ({ page }) => {
  await page.route('**/api/v1/auth/tokens/refresh', (route) =>
    json(route, { access_token: 'worker-token', user: worker }, 201));
  await page.route('**/api/v1/remnant-materials**', (route) => json(route, []));

  await page.goto('/remnants?tab=auto');

  await expect(page.getByRole('tab', { name: '自动导入' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '批量导入' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '材质管理' })).toBeVisible();
  await expect(page.getByRole('button', { name: '选择图纸' })).toBeVisible();
  await expect(page.getByText('选择/拖入文件夹')).toBeVisible();
});

test('folder selection preserves nested paths, prefills an editable project and posts aligned multipart fields', async ({ page }) => {
  let multipart = '';
  await page.route('**/api/v1/auth/tokens/refresh', (route) =>
    json(route, { access_token: 'worker-token', user: worker }, 201));
  await page.route('**/api/v1/remnant-materials**', (route) => json(route, [{
    id: 1, code: 'Q355B', family_code: 'Q355', enabled: true, aliases: [],
    created_at: now, updated_at: now,
  }]));
  await page.route('**/api/v1/remnant-import-batches/auto', async (route) => {
    multipart = (await route.request().postDataBuffer())?.toString('utf8') ?? '';
    await json(route, autoBatch, 202);
  });
  await page.route('**/api/v1/remnant-import-batches/88', (route) => json(route, autoBatch));

  await page.goto('/remnants?tab=auto');
  await page.locator('input[webkitdirectory]').evaluate((node) => {
    const transfer = new DataTransfer();
    const nested = new File(['DXF-A'], '一号板.dxf', { type: 'application/dxf' });
    Object.defineProperty(nested, 'webkitRelativePath', { value: '项目甲/一层/一号板.dxf' });
    const ignored = new File(['notes'], '说明.txt', { type: 'text/plain' });
    Object.defineProperty(ignored, 'webkitRelativePath', { value: '项目甲/说明.txt' });
    transfer.items.add(nested);
    transfer.items.add(ignored);
    Object.defineProperty(node, 'files', { configurable: true, value: transfer.files });
    node.dispatchEvent(new Event('change', { bubbles: true }));
  });

  await expect(page.getByLabel('项目编号')).toHaveValue('项目甲');
  await expect(page.getByText('一层/一号板.dxf')).toBeVisible();
  await expect(page.getByText('已忽略 1 个非 DWG/DXF 文件')).toBeVisible();
  await page.getByLabel('项目编号').fill('PJ-EDITED');
  await page.getByRole('button', { name: '确认并开始解析' }).click();

  await expect(page).toHaveURL(/tab=auto.*batch=88/);
  expect(multipart).toContain('name="files"; filename="');
  expect(multipart).toContain('name="relative_paths"');
  expect(multipart).toContain('一层/一号板.dxf');
  expect(multipart).toContain('name="project_no"');
  expect(multipart).toContain('PJ-EDITED');
  expect(multipart).toContain('name="folder_name"');
  expect(multipart).toContain('项目甲');

  await page.reload();
  await expect(page.getByText('批次 #88')).toBeVisible();
  await expect(page.getByText('-12.5 × 1000 X 2000')).toBeVisible();
  await expect(page.getByText('1000 × 2000')).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Row 1 selected' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'Select row 2' })).toBeDisabled();
});

test('empty project and file limits stop automatic upload with Chinese feedback', async ({ page }) => {
  let createCalls = 0;
  await page.route('**/api/v1/auth/tokens/refresh', (route) =>
    json(route, { access_token: 'worker-token', user: worker }, 201));
  await page.route('**/api/v1/remnant-materials**', (route) => json(route, []));
  await page.route('**/api/v1/remnant-import-batches/auto', async (route) => {
    createCalls += 1;
    await json(route, autoBatch, 202);
  });
  await page.goto('/remnants?tab=auto');

  const drawingInput = page.getByLabel('选择图纸文件', { exact: true });
  await drawingInput.setInputFiles([
    { name: '可用.dxf', mimeType: 'application/dxf', buffer: Buffer.from('DXF') },
    { name: '忽略.txt', mimeType: 'text/plain', buffer: Buffer.from('TEXT') },
  ]);
  await expect(page.getByText('已忽略 1 个非 DWG/DXF 文件')).toBeVisible();
  await page.getByRole('button', { name: '确认并开始解析' }).click();
  await expect(page.getByText('请填写项目编号')).toBeVisible();
  expect(createCalls).toBe(0);

  await drawingInput.setInputFiles(Array.from({ length: 101 }, (_, index) => ({
    name: `图纸-${index + 1}.dxf`,
    mimeType: 'application/dxf',
    buffer: Buffer.from('DXF'),
  })));
  await expect(page.getByText('一次最多选择 100 张图纸')).toBeVisible();
  expect(createCalls).toBe(0);
});
