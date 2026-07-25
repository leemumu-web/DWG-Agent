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
    ['drawing_processing', '图纸拆板与独立校验', 'automated', 'implemented', 'drawing_processing'],
    ['excel_stage1', 'Excel 第一阶段处理', 'automated', 'implemented', 'excel_stage1'],
    ['excel_stage2', 'Excel 第二阶段处理', 'placeholder', 'placeholder', 'excel_stage2'],
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
    config_json: { definition_revision: 4 },
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
      required_inputs: code === 'excel_stage1'
        ? ['source_excel', 'processed_dxf', 'bh_split_ledger']
        : [],
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
  await page.route(
    '**/api/v1/workflows/41/stages/excel_stage1/preflight',
    (route) => json(route, {
      ready: true,
      source_file_id: 701,
      source_file_name: '体育馆构件清单.xlsx',
      input_contract_version: 1,
      split_run_id: 88,
      official_pair_count: 40,
      checks: [
        { code: 'input_batch_frozen', label: '冻结输入清单有效' },
        { code: 'source_excel_unique', label: '唯一 Excel 来源一致' },
        { code: 'source_object_verified', label: '对象摘要与冻结记录一致' },
        { code: 'excel_contract_verified', label: 'Excel 表结构符合输入合同' },
        { code: 'split_handoff_verified', label: '正式拆板结果成对且可用' },
      ],
    }),
  );
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
  await expect(page.getByText('运行前检查通过')).toBeVisible();
  await expect(page.getByText('正式拆板：40 对图纸')).toBeVisible();

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
    ['drawing_processing', '图纸拆板与独立校验', 'automated', 'implemented', 'drawing_processing'],
    ['excel_stage1', 'Excel 第一阶段处理', 'automated', 'implemented', 'excel_stage1'],
    ['excel_stage2', 'Excel 第二阶段处理', 'placeholder', 'placeholder', 'excel_stage2'],
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
    job_id: index === 1 ? 901 : code === 'windows_cam' ? 999 : null,
    job_attempt: index === 1 || code === 'windows_cam' ? 1 : null,
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
    ['classified_dxf', 805, 'member-02.dxf'],
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
  artifacts.push({
    id: 904,
    stage_run_id: 207,
    artifact_type: 'cam_output_dxf',
    file_id: 804,
    result_id: null,
    version: 1,
    metadata_json: { original_name: 'cam-output.dxf' },
    created_at: now,
    updated_at: now,
  });
  const workflow = {
    id: 42,
    project_id: 8,
    created_by: 1,
    name: '厂房钢构生产项目',
    workflow_type: 'linux_production',
    status: 'waiting_input',
    current_stage: 'dxf_classification',
    progress: 22,
    config_json: { definition_revision: 4 },
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
          ? [
              'processed_dxf',
              'weld_allowance_dxf',
              'split_report',
              'weld_allowance_report',
              'validation_report',
              'bh_split_ledger',
              'split_manifest',
            ]
          : [],
      required_outputs: code === 'dxf_classification'
        ? ['classified_dxf', 'classification_report', 'classification_manifest']
        : code === 'drawing_processing'
          ? [
              'processed_dxf',
              'weld_allowance_dxf',
              'split_report',
              'weld_allowance_report',
              'validation_report',
              'bh_split_ledger',
              'split_manifest',
            ]
          : [],
    })),
  };
  const classification = {
    id: 77,
    workflow_run_id: 42,
    status: 'completed_with_review',
    classifier_version: '1.2.0',
    report_schema: 'STEEL-DXF-CLASSIFICATION-1.2',
    cli_schema: 'STEEL-DXF-CLI-1.2',
    project_name: '厂房钢构生产项目',
    input_manifest_sha256: 'a'.repeat(64),
    input_count: 12,
    classified_count: 11,
    review_required_count: 1,
    unreadable_count: 0,
    type_counts: { PX: 3, XY: 8 },
    groups: [
      {
        group_key: 'status:review_required',
        label: '待确认',
        part_type: null,
        type_source: null,
        disposition: 'review_required',
        count: 1,
        warning_count: 1,
        total_size_bytes: 2048,
      },
      {
        group_key: 'type:PX',
        label: 'PX',
        part_type: 'PX',
        type_source: 'catalog',
        disposition: 'classified',
        count: 3,
        warning_count: 0,
        total_size_bytes: 6144,
      },
      {
        group_key: 'type:XY',
        label: 'XY',
        part_type: 'XY',
        type_source: 'auto_discovered',
        disposition: 'classified',
        count: 8,
        warning_count: 0,
        total_size_bytes: 16384,
      },
    ],
    report_file: { original_name: 'classification-report.json' },
    manifest_file: { original_name: 'classification-manifest.csv' },
    job: {
      id: 901,
      status: 'succeeded',
      progress: 100,
      attempt: 1,
    },
    items: [],
    error_code: null,
    error_message: null,
    started_at: now,
    finished_at: now,
    created_at: now,
    updated_at: now,
  };
  const pxDetails = {
    items: Array.from({ length: 3 }, (_, index) => ({
      output_name: `px-${index + 1}_拆板前.dxf`,
      part_type: 'PX',
      profile_raw: 'PX300*150*8',
      profile_normalized: 'PX300*150*8',
      type_source: 'catalog',
      disposition: 'classified',
      diagnostics: ['TITLE_PROFILE_PROVED'],
      size_bytes: 2048,
    })),
    total: 3,
    page: 1,
    page_size: 20,
  };
  let executionRequests = 0;
  let categoryArchiveRequests = 0;
  let allDxfArchiveRequests = 0;

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
  await page.route('**/api/v1/workflows/42/drawing-processing', (route) => json(route, null));
  await page.route('**/api/v1/workflows/42/batch-exports/preview', (route) => json(route, {
    workflow_id: 42,
    categories: [
      { key: 'classified_dxf', label: '原 DXF', file_count: 49, size_bytes: 4096, available: true },
      { key: 'processed_dxf', label: '正常拆板 DXF', file_count: 0, size_bytes: 0, available: false },
      { key: 'source_excel', label: '原 Excel', file_count: 1, size_bytes: 2048, available: true },
      { key: 'stage1_excel', label: '产出 Excel', file_count: 0, size_bytes: 0, available: false },
    ],
  }));
  await page.route(
    /\/api\/v1\/workflows\/42\/dxf-classification\/groups\/type(?:%3A|:)PX\?page=1&page_size=20$/,
    (route) => json(route, pxDetails),
  );
  await page.route('**/api/v1/workflows/42/stages/**/executions', async (route) => {
    executionRequests += 1;
    await json(route, {});
  });
  await page.route(
    /\/api\/v1\/workflows\/42\/dxf-classification\/groups\/type(?:%3A|:)PX\/download-archive$/,
    async (route) => {
      categoryArchiveRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/zip',
        headers: {
          'content-disposition': "attachment; filename*=UTF-8''workflow-42-dxf-PX.zip",
          'access-control-expose-headers': 'content-disposition',
        },
        body: 'PK-category-archive',
      });
    },
  );
  await page.route(
    '**/api/v1/workflows/42/dxf-classification/download-archive',
    async (route) => {
      allDxfArchiveRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/zip',
        headers: {
          'content-disposition': "attachment; filename*=UTF-8''workflow-42-all-classified-dxf.zip",
          'access-control-expose-headers': 'content-disposition',
        },
        body: 'PK-all-dxf-archive',
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

  await page.getByRole('button', { name: /图纸拆板与独立校验/ }).click();
  await expect(page.getByRole('heading', { name: '图纸拆板与独立校验' })).toBeVisible();
  await expect(page.getByText('该阶段尚未解锁')).toBeVisible();
  await expect(page.getByText('03 · 图纸拆板与独立校验')).toBeVisible();
  await expect(page.getByText('项目总进度')).toHaveCount(0);
  await expect(page.getByText('实时速度')).toHaveCount(0);
  await expect(page.getByText('未接入', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /开始整批拆板|确认当前阶段/ })).toHaveCount(0);
  const splitCard = page.locator('.workflow-dxf-split-panel');
  await expect(splitCard.getByRole('button', { name: '分批导出' })).toBeVisible();
  await expect(
    page.locator('.workflow-artifact-summary').getByRole('button', { name: '分批导出' }),
  ).toHaveCount(0);
  await splitCard.getByRole('button', { name: '分批导出' }).click();
  const exportDialog = page.getByRole('dialog', { name: '分批导出并释放服务器空间' });
  await expect(exportDialog.getByText('原 DXF', { exact: true })).toBeVisible();
  await expect(exportDialog.getByText('正常拆板 DXF', { exact: true })).toBeVisible();
  await expect(exportDialog.getByText('原 Excel', { exact: true })).toBeVisible();
  await expect(exportDialog.getByText('产出 Excel', { exact: true })).toBeVisible();
  await exportDialog.getByRole('button', { name: /取\s*消/ }).click();

  await page.getByRole('button', { name: '返回当前阶段' }).click();
  await expect(page.getByRole('heading', { name: 'DXF 分类与分流' })).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(page.getByText(/文件 #/)).toHaveCount(0);
  await expect(page.getByText('分类报告已纳入生产压缩包')).toHaveCount(0);
  await expect(page.getByText('分类清单已纳入生产压缩包')).toHaveCount(0);
  await expect(page.getByText('1 张图纸需要处理')).toBeVisible();
  await expect(page.getByRole('button', { name: /PX.*3 张.*内置类型/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /XY.*8 张.*自动发现/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /待确认.*1 张.*需要处理/ })).toBeVisible();

  await page.getByRole('button', { name: /PX.*3 张.*内置类型/ }).click();
  await expect(page.getByRole('dialog', { name: 'PX · 3 张 DXF' })).toBeVisible();
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('row')).toHaveCount(4);
  await expect(page.getByText('PX300*150*8').first()).toBeVisible();
  await expect(page.getByText(/文件 #/)).toHaveCount(0);

  const categoryDownloadPromise = page.waitForEvent('download');
  await page
    .getByRole('dialog', { name: 'PX · 3 张 DXF' })
    .getByRole('button', { name: '下载 PX 类 DXF' })
    .click();
  const categoryDownload = await categoryDownloadPromise;
  await expect.poll(() => categoryArchiveRequests).toBe(1);
  expect(categoryDownload.suggestedFilename()).toBe('workflow-42-dxf-PX.zip');

  await page.getByRole('dialog', { name: 'PX · 3 张 DXF' }).getByRole('button', { name: '关闭' }).click();
  await expect(page.getByRole('dialog', { name: 'PX · 3 张 DXF' })).toBeHidden();

  const allDownloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载全部 DXF' }).click();
  const allDownload = await allDownloadPromise;
  await expect.poll(() => allDxfArchiveRequests).toBe(1);
  expect(allDownload.suggestedFilename()).toBe('workflow-42-all-classified-dxf.zip');

  await page.getByRole('button', { name: /Excel 第一阶段处理/ }).click();
  await expect(page.getByRole('heading', { name: 'Excel 第一阶段处理' })).toBeVisible();
  await expect(page.getByText('该阶段尚未解锁')).toBeVisible();
  await expect(page.getByRole('button', { name: '运行 Excel 第一阶段' })).toHaveCount(0);
  expect(executionRequests).toBe(0);

  await page.getByRole('button', { name: /Excel 第二阶段处理/ }).click();
  await expect(page.getByRole('heading', { name: 'Excel 第二阶段处理' })).toBeVisible();
  await expect(page.getByText('能力等待上线')).toBeVisible();
  await expect(page.getByText('流程位置与数据接口已经预留')).toBeVisible();
  await expect(page.getByRole('button', { name: /开始|重试|确认当前阶段|下载本阶段结果压缩包/ })).toHaveCount(0);

  await page.getByRole('button', { name: /Windows CAM 排版/ }).click();
  await expect(page.getByRole('heading', { name: 'Windows CAM 排版' })).toBeVisible();
  await expect(page.getByText('能力等待上线')).toBeVisible();
  await expect(page.getByText('等待上线', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: '下载本阶段结果压缩包' })).toHaveCount(0);
  await expect(page.getByText(/任务 #999/)).toHaveCount(0);

  await expect(page.getByRole('heading', { name: '生产产物与证据' })).toBeVisible();
  await expect(page.getByText('已登记 5 项')).toBeVisible();
  await expect(page.getByText('classified_dxf × 2')).toBeVisible();
  await expect(page.getByText(/版本 v/)).toHaveCount(0);
  await expect(page.getByText(/已登记 ·/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: '下载全部' })).toBeEnabled();
});

test('partial split keeps downloads recoverable with operator guidance', async ({ page }) => {
  const stageDefinitions = [
    ['source_intake', '文件接收与输入冻结', 'manual', 'implemented', null],
    ['dxf_classification', 'DXF 分类与分流', 'automated', 'implemented', 'steel_dxf_classification'],
    ['drawing_processing', '图纸拆板与独立校验', 'automated', 'implemented', 'drawing_processing'],
    ['excel_stage1', 'Excel 第一阶段处理', 'automated', 'implemented', 'excel_stage1'],
    ['excel_stage2', 'Excel 第二阶段处理', 'placeholder', 'placeholder', 'excel_stage2'],
    ['design_barrier', '深化设计完整性屏障', 'manual', 'implemented', null],
    ['cam_packaging', 'CAM 工作包生成', 'placeholder', 'placeholder', 'cam_packaging'],
    ['windows_cam', 'Windows CAM 排版', 'external', 'external', 'windows_cam'],
    ['result_acceptance', 'CAM 结果接纳', 'placeholder', 'placeholder', 'result_acceptance'],
    ['delivery_archive', '交付与归档', 'manual', 'implemented', null],
  ] as const;
  const stages = stageDefinitions.map(([code, name], index) => ({
    id: 300 + index,
    stage_code: code,
    name,
    sequence: index + 1,
    status: index < 2 ? 'succeeded' : index === 2 ? 'running' : 'pending',
    job_id: index === 2 ? 930 : null,
    job_attempt: index === 2 ? 1 : null,
    progress: index <= 2 ? 100 : 0,
    input_json: null,
    output_json: null,
    error_code: null,
    error_message: null,
    started_at: index <= 2 ? now : null,
    finished_at: index <= 2 ? now : null,
    created_at: now,
    updated_at: now,
  }));
  const drawingArtifacts = [
    'processed_dxf',
    'weld_allowance_dxf',
    'split_report',
    'weld_allowance_report',
    'validation_report',
    'bh_split_ledger',
    'split_manifest',
  ].map((artifactType, index) => ({
    id: 1000 + index,
    stage_run_id: 302,
    artifact_type: artifactType,
    file_id: 1100 + index,
    result_id: null,
    version: 1,
    metadata_json: {
      original_name: artifactType === 'processed_dxf'
        ? 'BH-001_正常拆板.dxf'
        : `${artifactType}.json`,
      job_id: 930,
      job_attempt: 1,
    },
    created_at: now,
    updated_at: now,
  }));
  drawingArtifacts.push({
    id: 1099,
    stage_run_id: 302,
    artifact_type: 'processed_dxf',
    file_id: 1199,
    result_id: null,
    version: 1,
    metadata_json: {
      original_name: '旧尝试_正常拆板.dxf',
      job_id: 930,
      job_attempt: 0,
    },
    created_at: now,
    updated_at: now,
  });
  const workflow = {
    id: 43,
    project_id: 9,
    created_by: 1,
    name: '拆板部分成形批次',
    workflow_type: 'linux_production',
    status: 'running',
    current_stage: 'drawing_processing',
    progress: 33,
    config_json: { definition_revision: 4 },
    error_code: null,
    error_message: null,
    started_at: now,
    finished_at: null,
    created_at: now,
    updated_at: now,
    stages,
    artifacts: drawingArtifacts,
  };
  const advancedWorkflow = {
    ...workflow,
    status: 'waiting_input',
    current_stage: 'excel_stage1',
    progress: 44,
    stages: stages.map((stage) => (
      stage.stage_code === 'drawing_processing'
        ? { ...stage, status: 'succeeded', progress: 100, finished_at: now }
        : stage.stage_code === 'excel_stage1'
          ? { ...stage, status: 'waiting_input', started_at: now }
          : stage
    )),
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
      required_inputs: code === 'drawing_processing' ? ['classified_dxf'] : [],
      artifact_types: code === 'drawing_processing'
        ? [
            'processed_dxf',
            'weld_allowance_dxf',
            'split_report',
            'weld_allowance_report',
            'validation_report',
            'bh_split_ledger',
            'split_manifest',
          ]
        : [],
      required_outputs: code === 'drawing_processing'
        ? [
            'processed_dxf',
            'weld_allowance_dxf',
            'split_report',
            'weld_allowance_report',
            'validation_report',
            'bh_split_ledger',
            'split_manifest',
          ]
        : [],
    })),
  };
  const splitRun = {
    id: 88,
    workflow_run_id: 43,
    status: 'completed_with_review',
    splitter_version: '1.5.2',
    cli_schema: 'STEEL-DXF-SPLIT-CLI-1',
    validation_schema: 'STEEL-DXF-SPLIT-VALIDATION-1',
    input_manifest_sha256: 'b'.repeat(64),
    input_count: 3,
    processed_count: 3,
    failed_count: 0,
    reviewed_count: 0,
    elapsed_seconds: 18,
    throughput_per_minute: 10,
    estimated_remaining_seconds: 0,
    auto_accepted_count: 2,
    manual_review_count: 1,
    classifier_confirmed_count: 2,
    splitter_detected_count: 0,
    unresolved_count: 1,
    classification_input_count: 5,
    classification_only_count: 2,
    classification_only_type_counts: {
      PX: 1,
      review_required: 1,
    },
    source_contracts: {
      BH: 'project_tekla_bh_dxf_v1',
      BOX: 'project_tekla_box_dxf_v1',
    },
    bh_split_ledger_file: null,
    split_manifest_file: null,
    validation_report_file: null,
    job: { id: 930, status: 'succeeded', progress: 100, attempt: 1 },
    items: [{
      id: 502,
      drawing_id: 12,
      classification_item_id: 402,
      source_file_id: 302,
      source_name: 'BOX-AUTO_拆板前.dxf',
      classification_disposition: 'classified',
      classification_part_type: 'BOX',
      type_resolution: 'classifier_confirmed',
      part_type: 'BOX',
      profile_normalized: 'BOX600X400X20X24',
      family: 'BOX',
      source_contract_id: 'project_tekla_box_dxf_v1',
      automation_route: 'auto_accepted',
      disposition: 'auto_accepted',
      normal_dxf_file_id: 1201,
      weld_allowance_dxf_file_id: 1202,
      split_report_file_id: 1203,
      weld_allowance_report_file_id: 1204,
      diagnostics: [],
      validation: { status: 'passed' },
    }, {
      id: 503,
      drawing_id: 13,
      classification_item_id: 403,
      source_file_id: 303,
      source_name: 'BH-MISSING-XDATA_拆板前.dxf',
      classification_disposition: 'classified',
      classification_part_type: 'BH',
      type_resolution: 'classifier_confirmed',
      part_type: 'BH',
      profile_normalized: 'BH600X300X12X20',
      family: 'BH',
      source_contract_id: 'project_tekla_bh_dxf_v1',
      automation_route: 'failed',
      disposition: 'weld_allowance_failed',
      normal_dxf_file_id: null,
      weld_allowance_dxf_file_id: null,
      split_report_file_id: null,
      weld_allowance_report_file_id: null,
      diagnostics: ['WELD_ALLOWANCE_XDATA_MISSING'],
      validation: {
        status: 'failed',
        checks: {
          error_zh: '腹板轮廓无法证明唯一的余量伸长端，余量增长版未生成。',
        },
      },
    }],
    error_code: null,
    error_message: null,
    started_at: now,
    finished_at: now,
    created_at: now,
    updated_at: now,
  };
  let workflowReads = 0;
  const requestedExportCategories: string[][] = [];
  let selectivePreviewRequests = 0;
  let selectiveExportRequests = 0;

  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, {
    access_token: 'e2e-token',
    user,
  }, 201));
  await page.route('**/api/v1/workflows/projects?**', (route) => route.fulfill({
    json: {
      ...envelope([{ id: 9, code: 'REVIEW', name: '人工复核项目' }]),
      pagination: { page: 1, page_size: 200, total: 1, total_pages: 1 },
    },
  }));
  await page.route('**/api/v1/workflows/templates', (route) => json(route, [template]));
  await page.route('**/api/v1/workflows/43/drawing-processing', (route) => json(route, splitRun));
  await page.route('**/api/v1/workflows/43/batch-exports', async (route) => {
    const payload = route.request().postDataJSON() as { categories: string[] };
    requestedExportCategories.push(payload.categories);
    const split = payload.categories.includes('split_result_normal');
    const exportUid = split ? 'split-export' : 'original-export';
    await json(route, {
      export_uid: exportUid,
      workflow_run_id: 43,
      status: 'prepared',
      categories: payload.categories,
      file_count: split ? 4 : 5,
      source_size_bytes: 4096,
      filename: split ? '拆板正式结果.zip' : '本批原图.zip',
      download_url: `/api/v1/workflows/43/batch-exports/${exportUid}/download?token=e2e`,
      token_expires_at: now,
      downloaded_at: null,
      purged_at: null,
      purged_file_count: 0,
      purged_size_bytes: 0,
      error_code: null,
      error_message: null,
      created_at: now,
      updated_at: now,
    }, 201);
  });
  await page.route(
    /\/api\/v1\/workflows\/43\/batch-exports\/(?:split|original)-export$/,
    (route) => {
      const exportUid = route.request().url().includes('split-export')
        ? 'split-export'
        : 'original-export';
      return json(route, {
        export_uid: exportUid,
        workflow_run_id: 43,
        status: 'downloaded',
        categories: exportUid === 'split-export'
          ? ['split_result_normal', 'split_result_allowance']
          : ['classified_dxf'],
        file_count: exportUid === 'split-export' ? 4 : 5,
        source_size_bytes: 4096,
        filename: exportUid === 'split-export' ? '拆板正式结果.zip' : '本批原图.zip',
        download_url: `/api/v1/workflows/43/batch-exports/${exportUid}/download?token=e2e`,
        token_expires_at: now,
        downloaded_at: now,
        purged_at: null,
        purged_file_count: 0,
        purged_size_bytes: 0,
        error_code: null,
        error_message: null,
        created_at: now,
        updated_at: now,
      });
    },
  );
  await page.route(
    /\/api\/v1\/workflows\/43\/batch-exports\/(?:split|original)-export\/download\?token=e2e$/,
    (route) => route.fulfill({
      status: 200,
      contentType: 'application/zip',
      headers: {
        'content-disposition': route.request().url().includes('split-export')
          ? "attachment; filename*=UTF-8''%E6%8B%86%E6%9D%BF%E6%AD%A3%E5%BC%8F%E7%BB%93%E6%9E%9C.zip"
          : "attachment; filename*=UTF-8''%E6%9C%AC%E6%89%B9%E5%8E%9F%E5%9B%BE.zip",
      },
      body: 'PK-native-export',
    }),
  );
  await page.route('**/api/v1/workflows/43', (route) => {
    workflowReads += 1;
    return json(route, workflowReads === 1 ? workflow : advancedWorkflow);
  });
  await page.route(
    '**/api/v1/workflows/43/drawing-processing/runs/88/selective-export-preview',
    async (route) => {
      selectivePreviewRequests += 1;
      if (selectivePreviewRequests === 1) {
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({
            error: {
              code: 'DRAWING_SELECTIVE_EXPORT_STORAGE_UNAVAILABLE',
              message: '对象存储暂时不可用，未创建不完整压缩包。',
              details: {},
            },
            meta: { request_id: 'selective-preview-503', timestamp: now },
          }),
        });
        return;
      }
      await json(route, {
      workflow_id: 43,
      split_run_id: 88,
      categories: selectivePreviewRequests === 2 ? [
        { key: 'failed_bh', label: '未通过的 BH', file_count: 1, size_bytes: 1024, available: true },
        { key: 'failed_box', label: '未通过的 BOX', file_count: 0, size_bytes: 0, available: false },
        { key: 'pl', label: 'PL', file_count: 2, size_bytes: 2048, available: true },
        { key: 'other', label: '其他', file_count: 3, size_bytes: 3072, available: true },
      ] : [
        { key: 'failed_bh', label: '未通过的 BH', file_count: 0, size_bytes: 0, available: false },
        { key: 'failed_box', label: '未通过的 BOX', file_count: 0, size_bytes: 0, available: false },
        { key: 'pl', label: 'PL', file_count: 0, size_bytes: 0, available: false },
        { key: 'other', label: '其他', file_count: 0, size_bytes: 0, available: false },
      ],
      });
    },
  );
  await page.route(
    /\/api\/v1\/workflows\/43\/drawing-processing\/runs\/88\/selective-exports$/,
    async (route) => {
      selectiveExportRequests += 1;
      expect(route.request().postDataJSON()).toEqual({
        categories: ['failed_bh', 'pl', 'other'],
      });
      await json(route, {
        categories: ['failed_bh', 'pl', 'other'],
        file_count: 6,
        source_size_bytes: 6144,
        filename: 'workflow-43-split-run-88-selected-dxf.zip',
        download_url: '/api/v1/workflows/43/drawing-processing/runs/88/selective-exports/e2e/download',
        token_expires_at: now,
      }, 201);
    },
  );
  await page.route(
    '**/api/v1/workflows/43/drawing-processing/runs/88/selective-exports/e2e/download',
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/zip',
        headers: {
          'content-disposition': "attachment; filename*=UTF-8''workflow-43-split-run-88-selected-dxf.zip",
        },
        body: 'PK-selected-dxf',
      });
    },
  );

  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'e2e-token', savedUser: user });
  await page.goto('/workflows/43');

  await expect(page.getByRole('heading', { name: '图纸拆板与独立校验' })).toBeVisible();
  await expect(page.getByText('正在查看历史阶段')).toBeVisible();
  await expect(
    page.getByRole('button', { name: /Excel 第一阶段处理.*待输入.*当前阶段/ }),
  ).toBeVisible();
  await expect(page.getByText('正式配对图纸')).toBeVisible();
  await expect(page.getByText('未形成结果', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('alert').filter({ hasText: '3 张均已处理：2 张形成正式配对结果，1 张未形成' }),
  ).toBeVisible();
  await expect(
    page.getByText('腹板轮廓无法证明唯一的余量伸长端，余量增长版未生成。'),
  ).toBeVisible();
  await expect(page.getByText(/需人工处理的图纸/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: /重新整批拆板|完成复核/ })).toHaveCount(0);

  const splitDownloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载拆板结果 ZIP' }).click();
  const splitDownload = await splitDownloadPromise;
  expect(splitDownload.suggestedFilename()).toBe('拆板正式结果.zip');

  const originalDownloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载本批原图 ZIP（不含拆板成品）' }).click();
  const originalDownload = await originalDownloadPromise;
  expect(originalDownload.suggestedFilename()).toBe('本批原图.zip');

  await expect.poll(() => requestedExportCategories).toEqual([
    ['split_result_normal', 'split_result_allowance'],
    ['classified_dxf'],
  ]);

  await page.getByRole('button', { name: '分类图纸导出' }).click();
  const selectiveDialog = page.getByRole('dialog', { name: '选择要导出的图纸' });
  await expect(selectiveDialog.getByText('可导出图纸统计失败')).toBeVisible();
  await expect(selectiveDialog.getByText(/对象存储暂时不可用/)).toBeVisible();
  await expect(selectiveDialog.getByText(/DRAWING_SELECTIVE_EXPORT_STORAGE_UNAVAILABLE/)).toBeVisible();
  await expect(selectiveDialog.getByText(/请求 selective-preview-503/)).toBeVisible();
  await expect(selectiveDialog.getByText(/处理建议：服务器暂时无法完成操作/)).toBeVisible();
  await selectiveDialog.getByRole('button', { name: '重新检查' }).click();
  await expect(selectiveDialog.getByText('未通过的 BH', { exact: true })).toBeVisible();
  await expect(selectiveDialog.getByText('未通过的 BOX', { exact: true })).toBeVisible();
  await expect(selectiveDialog.getByText('PL', { exact: true })).toBeVisible();
  await expect(selectiveDialog.getByText('其他', { exact: true })).toBeVisible();
  const selectiveDownloadPromise = page.waitForEvent('download');
  await selectiveDialog.getByRole('button', { name: '下载所选 DXF' }).click();
  const selectiveDownload = await selectiveDownloadPromise;
  await expect.poll(() => selectiveExportRequests).toBe(1);
  expect(selectiveDownload.url()).toContain('/selective-exports/e2e/download');
  expect(selectiveDownload.suggestedFilename()).toBe(
    'workflow-43-split-run-88-selected-dxf.zip',
  );
  await expect(selectiveDialog).toBeVisible();
  await expect(selectiveDialog.getByText('下载已准备')).toBeVisible();
  await expect(selectiveDialog.getByText('6 个 DXF', { exact: true })).toBeVisible();
  await expect(selectiveDialog.getByRole('button', { name: '再次开始下载' })).toBeVisible();
  await selectiveDialog.getByRole('button', { name: '下载已开始，关闭' }).click();
  await expect(selectiveDialog).toBeHidden();

  await page.getByRole('button', { name: '分类图纸导出' }).click();
  await expect(selectiveDialog.getByText('当前没有可导出的文件')).toBeVisible();
  await expect(selectiveDialog.getByRole('button', { name: '下载所选 DXF' })).toBeDisabled();
});
