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
    config_json: { definition_revision: 3 },
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
      required_inputs: code === 'excel_stage1' ? ['source_excel'] : [],
      artifact_types: code === 'excel_stage1' ? ['stage1_excel'] : [],
      required_outputs: code === 'excel_stage1' ? ['stage1_excel'] : [],
    })),
  };
  let executionBody: unknown;

  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, {
    access_token: 'e2e-token',
    user,
  }, 201));
  await page.route('**/api/v1/workflows/projects?**', (route) => route.fulfill({
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
  await expect(page.getByText('图纸主格式：DXF')).toBeVisible();
  await expect(page.getByText(/完成必需产物：stage1_excel/)).toBeVisible();
  await expect(page.getByText('体育馆钢构生产批次')).toBeVisible();
  await expect(page.getByText('文件接收与输入冻结')).toBeVisible();
  await expect(page.getByText('交付与归档')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Excel 第一阶段处理' })).toBeVisible();
  await expect(page.getByText(/冻结输入中的 Excel/)).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Excel 输入文件' })).toHaveCount(0);

  await page.getByRole('button', { name: '运行 Excel 第一阶段' }).click();
  await expect.poll(() => executionBody).toEqual({ execution_kind: 'excel_stage1' });
  const failure = page.getByRole('alert', { name: '表格输入不符合规范' });
  await expect(failure).toContainText('原表 · 第 6 行 · 数量');
  await expect(failure).toContainText('请返回输入阶段，替换为包含数量列的 Tekla 原表。');
  await expect(failure).toContainText('请求 workflow-excel-invalid');
});

test('production route inspects stages safely and keeps classification output compact', async ({ page }) => {
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
    id: 200 + index,
    stage_code: code,
    name,
    sequence: index + 1,
    status: index === 0 ? 'succeeded' : index === 1 ? 'ready' : 'pending',
    job_id: index === 1 ? 901 : null,
    job_attempt: index === 1 ? 1 : null,
    progress: index === 0 ? 100 : index === 1 ? 100 : 0,
    input_json: null,
    output_json: null,
    error_code: null,
    error_message: null,
    started_at: index < 2 ? now : null,
    finished_at: index < 2 ? now : null,
    created_at: now,
    updated_at: now,
  }));
  const artifacts = [
    ['classified_dxf', 801, 'member-01.dxf'],
    ['classification_report', 802, 'classification-report.json'],
    ['classification_manifest', 803, 'classification-manifest.json'],
  ].map(([artifactType, fileId, originalName], index) => ({
    id: 900 + index,
    stage_run_id: 201,
    artifact_type: artifactType,
    file_id: fileId,
    result_id: null,
    version: 1,
    metadata_json: { original_name: originalName },
    created_at: now,
    updated_at: now,
  }));
  const workflow = {
    id: 42,
    project_id: 8,
    created_by: 1,
    name: '厂房钢构生产项目',
    workflow_type: 'linux_production',
    status: 'waiting_input',
    current_stage: 'dxf_classification',
    progress: 22,
    config_json: { definition_revision: 3 },
    error_code: null,
    error_message: null,
    started_at: now,
    finished_at: null,
    created_at: now,
    updated_at: now,
    stages,
    artifacts,
  };
  const template = {
    code: 'linux_production',
    name: 'Linux 生产流程',
    description: '服务器端生产编排框架',
    stages: stageDefinitions.map(([code, name, mode, status, kind]) => ({
      code,
      name,
      description: `${name}阶段说明`,
      execution_mode: mode,
      implementation_status: status,
      execution_kind: kind,
      required_inputs: code === 'dxf_classification'
        ? ['canonical_dxf']
        : code === 'drawing_processing'
          ? ['classified_dxf']
          : [],
      artifact_types: code === 'dxf_classification'
        ? ['classified_dxf', 'classification_report', 'classification_manifest']
        : code === 'drawing_processing'
          ? ['processed_dxf', 'validation_report']
          : [],
      required_outputs: code === 'dxf_classification'
        ? ['classified_dxf', 'classification_report', 'classification_manifest']
        : code === 'drawing_processing'
          ? ['processed_dxf', 'validation_report']
          : [],
    })),
  };
  const classificationItems = Array.from({ length: 12 }, (_, index) => ({
    id: 1000 + index,
    drawing_id: 1100 + index,
    source_file: {
      id: 1200 + index,
      original_name: `member-${String(index + 1).padStart(2, '0')}.dxf`,
      file_ext: '.dxf',
      size_bytes: 2048,
    },
    output_file: {
      id: 1300 + index,
      original_name: `member-${String(index + 1).padStart(2, '0')}_拆板前.dxf`,
      file_ext: '.dxf',
      size_bytes: 2048,
    },
    source_name: `member-${String(index + 1).padStart(2, '0')}.dxf`,
    output_name: `member-${String(index + 1).padStart(2, '0')}_拆板前.dxf`,
    output_directory: '厂房钢构生产项目_BH_dxf',
    disposition: index === 11 ? 'review_required' : 'classified',
    part_type: 'BH',
    diagnostics: index === 11 ? ['截面字段需要人工确认'] : [],
  }));
  const classification = {
    id: 77,
    workflow_run_id: 42,
    status: 'completed_with_review',
    classifier_version: '1.1.0',
    report_schema: 'steel-dxf-classifier-report/1',
    cli_schema: 'steel-dxf-classifier-cli/1',
    project_name: '厂房钢构生产项目',
    input_manifest_sha256: 'a'.repeat(64),
    input_count: 12,
    classified_count: 11,
    review_required_count: 1,
    unreadable_count: 0,
    type_counts: { BH: 12 },
    report_file: classificationItems[0].output_file,
    manifest_file: classificationItems[1].output_file,
    job: {
      id: 901,
      status: 'succeeded',
      progress: 100,
      attempt: 1,
    },
    items: classificationItems,
    error_code: null,
    error_message: null,
    started_at: now,
    finished_at: now,
    created_at: now,
    updated_at: now,
  };
  let executionRequests = 0;
  let stageArchiveRequests = 0;

  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, {
    access_token: 'e2e-token',
    user,
  }, 201));
  await page.route('**/api/v1/workflows/projects?**', (route) => route.fulfill({
    json: {
      ...envelope([{ id: 8, code: 'PLANT', name: '厂房钢构项目' }]),
      pagination: { page: 1, page_size: 200, total: 1, total_pages: 1 },
    },
  }));
  await page.route('**/api/v1/workflows/templates', (route) => json(route, [template]));
  await page.route('**/api/v1/workflows/42/dxf-classification', (route) => json(route, classification));
  await page.route('**/api/v1/workflows/42/stages/**/executions', async (route) => {
    executionRequests += 1;
    await json(route, {});
  });
  await page.route(
    '**/api/v1/workflows/42/stages/dxf_classification/download-archive',
    async (route) => {
      stageArchiveRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/zip',
        headers: {
          'content-disposition': "attachment; filename*=UTF-8''workflow-42-02_dxf_classification.zip",
          'access-control-expose-headers': 'content-disposition',
        },
        body: 'PK-stage-archive',
      });
    },
  );
  await page.route('**/api/v1/workflows/42', (route) => json(route, workflow));

  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'e2e-token', savedUser: user });
  await page.goto('/workflows/42');

  await page.getByRole('button', { name: /图纸分类与拆板/ }).click();
  await expect(page.getByRole('heading', { name: '图纸分类与拆板' })).toBeVisible();
  await expect(page.getByText('该阶段尚未解锁')).toBeVisible();
  await expect(page.getByText('拆板执行能力尚未接入')).toBeVisible();
  await expect(page.getByText('项目总进度')).toBeVisible();
  await expect(page.getByText('实时速度')).toBeVisible();
  await expect(page.getByText('未接入', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: /开始|重试|确认当前阶段/ })).toHaveCount(0);

  await page.getByRole('button', { name: '返回当前阶段' }).click();
  await expect(page.getByRole('heading', { name: 'DXF 分类与分流' })).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(page.getByText(/文件 #/)).toHaveCount(0);

  await page.getByRole('button', { name: '查看文件明细（12）' }).click();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('row')).toHaveCount(11);
  await expect(page.getByText(/文件 #/)).toHaveCount(0);

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载分流结果压缩包' }).click();
  const download = await downloadPromise;
  await expect.poll(() => stageArchiveRequests).toBe(1);
  expect(download.suggestedFilename()).toBe('workflow-42-02_dxf_classification.zip');

  await page.getByRole('button', { name: /Excel 第一阶段处理/ }).click();
  await expect(page.getByRole('heading', { name: 'Excel 第一阶段处理' })).toBeVisible();
  await expect(page.getByText('该阶段尚未解锁')).toBeVisible();
  await expect(page.getByRole('button', { name: '运行 Excel 第一阶段' })).toHaveCount(0);
  expect(executionRequests).toBe(0);
});
