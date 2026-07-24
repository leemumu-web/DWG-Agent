import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-24T08:00:00Z';
const envelope = (data: unknown, requestId = 'workflow-detail-e2e') => ({
  data,
  meta: { request_id: requestId, timestamp: now },
});
const user = {
  id: 1,
  username: 'operator',
  real_name: '生产操作员',
  status: 'active',
  roles: [{ id: 1, code: 'super_admin', name: '管理员', is_system: true }],
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

test('workflow detail runs frozen Excel stage without a second file selector', async ({ page }) => {
  const stageDefinitions = [
    ['source_intake', '文件接收与输入冻结', 'manual', 'implemented', null],
    ['dxf_classification', 'DXF 分类与分流', 'automated', 'implemented', 'steel_dxf_classification'],
    ['drawing_processing', '图纸分类与拆板', 'placeholder', 'placeholder', 'drawing_processing'],
    ['excel_stage1', 'Excel 第一阶段处理', 'automated', 'implemented', 'excel_stage1'],
    ['design_barrier', '深化设计完整性屏障', 'manual', 'implemented', null],
    ['cam_packaging', 'CAM 工作包生成', 'placeholder', 'placeholder', 'cam_packaging'],
    ['windows_cam', 'Windows CAM 排版', 'external', 'external', 'windows_cam'],
    ['result_acceptance', 'CAM 结果接纳', 'placeholder', 'placeholder', 'result_acceptance'],
    ['delivery_archive', '交付与归档', 'manual', 'implemented', null],
  ] as const;
  const stages = stageDefinitions.map(([code, name], index) => ({
    id: 100 + index,
    stage_code: code,
    name,
    sequence: index + 1,
    status: index < 3 ? 'succeeded' : index === 3 ? 'waiting_input' : 'pending',
    job_id: null,
    job_attempt: null,
    progress: index < 3 ? 100 : 0,
    input_json: null,
    output_json: null,
    error_code: null,
    error_message: null,
    started_at: index < 4 ? now : null,
    finished_at: index < 3 ? now : null,
    created_at: now,
    updated_at: now,
  }));
  const workflow = {
    id: 41,
    project_id: 7,
    created_by: 1,
    name: '体育馆钢构生产批次',
    workflow_type: 'linux_production',
    status: 'waiting_input',
    current_stage: 'excel_stage1',
    progress: 33,
    config_json: { definition_revision: 2 },
    error_code: null,
    error_message: null,
    started_at: now,
    finished_at: null,
    created_at: now,
    updated_at: now,
    stages,
    artifacts: [{
      id: 801,
      stage_run_id: 100,
      artifact_type: 'source_excel',
      file_id: 701,
      result_id: null,
      version: 1,
      metadata_json: { original_name: '体育馆构件清单.xlsx' },
      created_at: now,
      updated_at: now,
    }],
  };
  const template = {
    code: 'linux_production',
    name: 'Linux 生产流程',
    description: '服务器端生产编排框架',
    stages: stageDefinitions.map(([code, name, mode, status, kind]) => ({
      code,
      name,
      description: code === 'excel_stage1'
        ? '处理冻结的原始 Tekla Excel，生成整理表和 part。'
        : `${name}阶段说明`,
      execution_mode: mode,
      implementation_status: status,
      execution_kind: kind,
      required_inputs: code === 'excel_stage1' ? ['frozen_source_excel'] : [],
      artifact_types: code === 'excel_stage1' ? ['stage1_excel'] : [],
    })),
  };
  let executionBody: unknown;

  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, {
    access_token: 'e2e-token',
    user,
  }, 201));
  await page.route('**/api/v1/projects?**', (route) => route.fulfill({
    json: {
      ...envelope([{ id: 7, code: 'CAP', name: '首都体育学院' }]),
      pagination: { page: 1, page_size: 200, total: 1, total_pages: 1 },
    },
  }));
  await page.route('**/api/v1/workflows/templates', (route) => json(route, [template]));
  await page.route('**/api/v1/workflows/41/stages/excel_stage1/executions', async (route) => {
    executionBody = route.request().postDataJSON();
    await route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'EXCEL_INPUT_REQUIRED_COLUMNS_MISSING',
          message: '表格缺少 Excel 第一阶段所需列。',
          details: {
            failure: {
              code: 'EXCEL_INPUT_REQUIRED_COLUMNS_MISSING',
              message: '表格缺少 Excel 第一阶段所需列。',
              action: '请返回输入阶段，替换为包含数量列的 Tekla 原表。',
              contract_version: 1,
              issues: [{
                sheet: '原表',
                row: 6,
                column: null,
                field: '数量',
                value: null,
                reason: 'required_column_missing',
              }],
              sheets: ['原表'],
              meta: { issue_count: 1 },
            },
          },
        },
        meta: { request_id: 'workflow-excel-invalid' },
      }),
    });
  });
  await page.route('**/api/v1/workflows/41', (route) => json(route, workflow));

  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'e2e-token', savedUser: user });
  await page.goto('/workflows/41');

  await expect(page.getByRole('heading', { name: '生产批次 #41' })).toBeVisible();
  await expect(page.getByText('体育馆钢构生产批次')).toBeVisible();
  await expect(page.getByText('文件接收与输入冻结')).toBeVisible();
  await expect(page.getByText('交付与归档')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Excel 第一阶段处理' })).toBeVisible();
  await expect(page.getByText(/冻结输入中的 Excel/)).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Excel 输入文件' })).toHaveCount(0);
  await expect(page.getByText(/DXF.?Excel/i)).toHaveCount(0);

  await page.getByRole('button', { name: '运行 Excel 第一阶段' }).click();
  expect(executionBody).toEqual({ execution_kind: 'excel_stage1' });
  const failure = page.getByRole('alert', { name: '表格输入不符合规范' });
  await expect(failure).toContainText('原表 · 第 6 行 · 数量');
  await expect(failure).toContainText('请返回输入阶段，替换为包含数量列的 Tekla 原表。');
  await expect(failure).toContainText('请求 workflow-excel-invalid');
});
