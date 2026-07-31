import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-31T03:00:00Z';
const user = {
  id: 1,
  username: 'operator',
  real_name: '生产操作员',
  status: 'active',
  roles: [{ id: 1, code: 'super_admin', name: '管理员', is_system: true }],
  created_at: now,
  updated_at: now,
};

const stageDefinitions = [
  ['source_intake', '文件接收与输入冻结', 'manual', 'implemented', null],
  ['dxf_classification', 'DXF 分类与分流', 'automated', 'implemented', 'steel_dxf_classification'],
  ['drawing_processing', '图纸拆板与独立校验', 'automated', 'implemented', 'drawing_processing'],
  ['excel_stage1', 'Excel 第一阶段处理', 'automated', 'implemented', 'excel_stage1'],
  ['excel_stage2', 'Excel 第二阶段处理', 'automated', 'implemented', 'excel_stage2'],
  ['design_barrier', '深化设计完整性屏障', 'manual', 'implemented', null],
  ['cam_packaging', 'CAM 工作包生成', 'placeholder', 'placeholder', 'cam_packaging'],
  ['windows_cam', 'Windows CAM 排版', 'external', 'external', 'windows_cam'],
  ['result_acceptance', 'CAM 结果接纳', 'placeholder', 'placeholder', 'result_acceptance'],
  ['delivery_archive', '交付与归档', 'manual', 'implemented', null],
] as const;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'excel-stage2-e2e', timestamp: now } };
}

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(envelope(data)),
  });
}

function workflow(stage2Status: 'waiting_input' | 'queued' | 'running' | 'succeeded') {
  const stage2Index = stageDefinitions.findIndex(([code]) => code === 'excel_stage2');
  const stage2Active = ['queued', 'running'].includes(stage2Status);
  const stages = stageDefinitions.map(([code, name], index) => ({
    id: 700 + index,
    stage_code: code,
    name,
    sequence: index + 1,
    status: index < stage2Index
      ? 'succeeded'
      : index === stage2Index
        ? stage2Status
        : 'pending',
    job_id: index === stage2Index
      ? stage2Status === 'waiting_input' ? null : 880
      : index === 3 ? 879 : null,
    job_attempt: index === stage2Index
      ? stage2Status === 'waiting_input' ? null : 1
      : index === 3 ? 1 : null,
    progress: index < stage2Index
      ? 100
      : index === stage2Index
        ? stage2Status === 'succeeded' ? 100 : stage2Active ? 42 : 0
        : 0,
    input_json: null,
    output_json: null,
    error_code: null,
    error_message: null,
    started_at: index <= stage2Index ? now : null,
    finished_at: index < stage2Index || stage2Status === 'succeeded' && index === stage2Index
      ? now
      : null,
    created_at: now,
    updated_at: now,
  }));
  return {
    id: 81,
    project_id: 17,
    created_by: 1,
    name: 'BH 左右进深化批次',
    workflow_type: 'linux_production',
    status: stage2Status === 'succeeded'
      ? 'waiting_review'
      : stage2Active ? 'running' : 'waiting_input',
    current_stage: stage2Status === 'succeeded' ? 'design_barrier' : 'excel_stage2',
    progress: stage2Status === 'succeeded' ? 55 : 50,
    config_json: { definition_revision: 4 },
    error_code: null,
    error_message: null,
    started_at: now,
    finished_at: null,
    created_at: now,
    updated_at: now,
    stages,
    artifacts: [
      {
        id: 981,
        stage_run_id: 700,
        artifact_type: 'source_excel',
        file_id: 910,
        result_id: null,
        version: 1,
        metadata_json: { original_name: '构件清单.xlsx' },
        created_at: now,
        updated_at: now,
      },
      {
        id: 982,
        stage_run_id: 703,
        artifact_type: 'stage1_excel',
        file_id: 911,
        result_id: 920,
        version: 1,
        metadata_json: { job_id: 879, job_attempt: 1 },
        created_at: now,
        updated_at: now,
      },
      ...(stage2Status === 'succeeded' ? [
        {
          id: 983,
          stage_run_id: 704,
          artifact_type: 'bh_setback_excel',
          file_id: 912,
          result_id: 921,
          version: 1,
          metadata_json: { job_id: 880, job_attempt: 1 },
          created_at: now,
          updated_at: now,
        },
        {
          id: 984,
          stage_run_id: 704,
          artifact_type: 'stage2_excel',
          file_id: 913,
          result_id: 922,
          version: 1,
          metadata_json: { job_id: 880, job_attempt: 1 },
          created_at: now,
          updated_at: now,
        },
      ] : []),
    ],
  };
}

function workflowAtExcelStage1(): ReturnType<typeof workflow> {
  const detail = workflow('waiting_input');
  const stage1Index = stageDefinitions.findIndex(([code]) => code === 'excel_stage1');
  return {
    ...detail,
    status: 'waiting_input',
    current_stage: 'excel_stage1',
    progress: 40,
    stages: detail.stages.map((stage, index) => ({
      ...stage,
      status: index < stage1Index
        ? 'succeeded'
        : index === stage1Index ? 'waiting_input' : 'pending',
      job_id: index < stage1Index ? stage.job_id : null,
      job_attempt: index < stage1Index ? stage.job_attempt : null,
      progress: index < stage1Index ? 100 : 0,
      finished_at: index < stage1Index ? now : null,
    })),
    artifacts: detail.artifacts.filter((artifact) => artifact.artifact_type === 'source_excel'),
  } as ReturnType<typeof workflow>;
}

const template = {
  code: 'linux_production',
  name: 'Linux 生产流程',
  description: '服务器端生产编排框架',
  stages: stageDefinitions.map(([code, name, executionMode, implementationStatus, executionKind]) => ({
    code,
    name,
    description: code === 'excel_stage2'
      ? '读取分类阶段冻结的拆板前 BH 图纸，提取左右进并深化第一阶段 Excel。'
      : `${name}阶段说明`,
    execution_mode: executionMode,
    implementation_status: implementationStatus,
    execution_kind: executionKind,
    required_inputs: code === 'excel_stage2' ? ['stage1_excel', 'classified_dxf'] : [],
    artifact_types: code === 'excel_stage2' ? ['bh_setback_excel', 'stage2_excel'] : [],
    required_outputs: code === 'excel_stage2' ? ['bh_setback_excel', 'stage2_excel'] : [],
  })),
};

async function authenticate(page: Page) {
  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'excel-stage2-token', savedUser: user });
}

async function mockSharedApis(page: Page, detail: ReturnType<typeof workflow>) {
  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, {
    access_token: 'excel-stage2-token',
    user,
  }, 201));
  await page.route('**/api/v1/workflows/projects?**', (route) => route.fulfill({
    json: {
      ...envelope([{ id: 17, code: 'BH-TEST', name: 'BH 左右进项目' }]),
      pagination: { page: 1, page_size: 200, total: 1, total_pages: 1 },
    },
  }));
  await page.route('**/api/v1/workflows/templates', (route) => json(route, [template]));
  await page.route('**/api/v1/workflows/81', (route) => json(route, detail));
}

test('Excel 第二阶段预检冻结 BH 清单并只提交当前项目的深化任务', async ({ page }) => {
  let detail = workflow('waiting_input');
  let executionBody: unknown;
  await mockSharedApis(page, detail);
  await page.route('**/api/v1/workflows/81', (route) => json(route, detail));
  await page.route('**/api/v1/workflows/81/stages/excel_stage2/preflight', (route) => json(route, {
    ready: true,
    mode: 'bh_enhancement',
    stage1_file_id: 911,
    stage1_file_name: '构件清单_第一阶段.xlsx',
    stage1_job_id: 879,
    stage1_job_attempt: 1,
    classification_run_id: 713,
    classification_job_id: 714,
    classification_job_attempt: 1,
    bh_input_count: 2,
    checks: [
      { code: 'stage1_workbook_verified', label: '第一阶段唯一正式 Excel 可用' },
      { code: 'bh_batch_frozen', label: '已冻结 2 张拆板前 BH 图纸' },
    ],
  }));
  await page.route('**/api/v1/workflows/81/stages/excel_stage2/executions', async (route) => {
    executionBody = route.request().postDataJSON();
    detail = workflow('queued');
    await json(route, {
      workflow: detail,
      job: { id: 880, attempt: 1, status: 'queued', progress: 0 },
      reused: false,
      retried: false,
    }, 202);
  });

  await authenticate(page);
  await page.goto('/workflows/81');

  await expect(page.getByRole('heading', { name: 'Excel 第二阶段处理' })).toBeVisible();
  await expect(page.getByText('能力等待上线')).toHaveCount(0);
  await expect(page.getByText('第二阶段输入核验通过')).toBeVisible();
  await expect(page.getByText('构件清单_第一阶段.xlsx')).toBeVisible();
  await expect(page.getByText('已冻结 2 张拆板前 BH 图纸')).toBeVisible();
  await expect(
    page.getByText('以零件号匹配；翼板唯一时直接填写，多种翼板时按方案增行，并同步整理表与 part 表'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '处理 BH 的左右进' })).toBeEnabled();

  await page.getByRole('button', { name: '处理 BH 的左右进' }).click();
  await expect.poll(() => executionBody).toEqual({ execution_kind: 'excel_stage2' });
  await expect(page.getByText('BH 左右进任务已进入处理队列')).toBeVisible();
  await expect(
    page.getByRole('button').filter({ hasText: '正在处理 BH 的左右进' }),
  ).toBeDisabled();
});

test('Excel 第二阶段运行期间禁止重复提交并持续展示真实任务状态', async ({ page }) => {
  const detail = workflow('running');
  let preflightRequests = 0;
  let executionRequests = 0;
  await mockSharedApis(page, detail);
  await page.route('**/api/v1/workflows/81/stages/excel_stage2/preflight', async (route) => {
    preflightRequests += 1;
    await json(route, {
      ready: true,
      mode: 'bh_enhancement',
      stage1_file_id: 911,
      stage1_file_name: '构件清单_第一阶段.xlsx',
      stage1_job_id: 879,
      stage1_job_attempt: 1,
      classification_run_id: 713,
      classification_job_id: 714,
      classification_job_attempt: 1,
      bh_input_count: 2,
      checks: [],
    });
  });
  await page.route('**/api/v1/workflows/81/stages/excel_stage2/executions', async (route) => {
    executionRequests += 1;
    await json(route, {}, 202);
  });

  await authenticate(page);
  await page.goto('/workflows/81');

  await expect(page.getByText('服务器正在处理 BH 左右进')).toBeVisible();
  await expect(page.getByText(/任务 #880/)).toBeVisible();
  await expect(page.getByTitle('42%')).toBeVisible();
  await expect(
    page.getByRole('button').filter({ hasText: '正在处理 BH 的左右进' }),
  ).toBeDisabled();
  await expect.poll(() => preflightRequests).toBe(0);
  await expect.poll(() => executionRequests).toBe(0);
});

test('Excel 第二阶段预检失败时只展示工人可执行的修正信息', async ({ page }) => {
  const detail = workflow('waiting_input');
  await mockSharedApis(page, detail);
  await page.route('**/api/v1/workflows/81/stages/excel_stage2/preflight', (route) => (
    route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'EXCEL_STAGE2_STAGE1_RESULT_STALE',
          message: '第一阶段正式 Excel 与当前任务记录不一致。',
          details: {
            failure: {
              code: 'EXCEL_STAGE2_STAGE1_RESULT_STALE',
              message: '第一阶段正式 Excel 与当前任务记录不一致。',
              action: '请返回 Excel 第一阶段重新处理，再回到本阶段。',
              contract_version: 1,
              issues: [{
                sheet: '整理表',
                row: null,
                column: null,
                field: '第一阶段正式结果',
                value: null,
                reason: 'stale_stage1_result',
              }],
              sheets: ['整理表'],
              meta: { issue_count: 1 },
            },
          },
        },
        meta: { request_id: 'excel-stage2-stale-result' },
      }),
    })
  ));

  await authenticate(page);
  await page.goto('/workflows/81');

  await expect(page.getByRole('alert', { name: '表格输入不符合规范' })).toBeVisible();
  await expect(page.getByText('第一阶段正式 Excel 与当前任务记录不一致。')).toBeVisible();
  await expect(page.getByText('请返回 Excel 第一阶段重新处理，再回到本阶段。')).toBeVisible();
  await expect(page.getByText('请求 excel-stage2-stale-result')).toBeVisible();
  await expect(page.getByRole('button', { name: '处理 BH 的左右进' })).toBeDisabled();
  await expect(page.getByText(/Traceback|SQLAlchemy|\/home\//i)).toHaveCount(0);
});

test('Excel 第二阶段未启用时不误导重试且只请求一次', async ({ page }) => {
  const detail = workflow('waiting_input');
  let preflightRequests = 0;
  await mockSharedApis(page, detail);
  await page.route('**/api/v1/workflows/81/stages/excel_stage2/preflight', async (route) => {
    preflightRequests += 1;
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'EXCEL_STAGE2_PIPELINE_DISABLED',
          message: 'Excel 第二阶段处理服务当前未启用。',
          details: {},
        },
        meta: { request_id: 'stage2-disabled-r36' },
      }),
    });
  });

  await authenticate(page);
  await page.goto('/workflows/81');

  await expect(page.getByText('Excel 第二阶段处理服务当前未启用。')).toBeVisible();
  await expect(page.getByText('当前部署未开启 Excel 第二阶段处理，请联系管理员检查服务配置。')).toBeVisible();
  await expect(page.getByText('EXCEL_STAGE2_PIPELINE_DISABLED')).toHaveCount(0);
  await expect(page.getByText(/稍后重试一次/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: '重新检查' })).toHaveCount(0);
  await expect.poll(() => preflightRequests).toBe(1);
});

test('Excel 第一阶段未启用时使用中文业务标题且不显示错误码', async ({ page }) => {
  const detail = workflowAtExcelStage1();
  let preflightRequests = 0;
  await mockSharedApis(page, detail);
  await page.route('**/api/v1/workflows/81/stages/excel_stage1/preflight', async (route) => {
    preflightRequests += 1;
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'EXCEL_STAGE1_PIPELINE_DISABLED',
          message: 'Excel 第一阶段处理服务当前未启用。',
          details: {},
        },
        meta: { request_id: 'stage1-disabled-r36' },
      }),
    });
  });

  await authenticate(page);
  await page.goto('/workflows/81');

  await expect(page.getByText('Excel 第一阶段运行前检查未通过')).toBeVisible();
  await expect(page.getByText('Excel 第一阶段处理服务当前未启用。')).toBeVisible();
  await expect(page.getByText('当前部署未开启 Excel 第一阶段处理，请联系管理员检查服务配置。')).toBeVisible();
  await expect(page.getByText('EXCEL_STAGE1_PIPELINE_DISABLED')).toHaveCount(0);
  await expect(page.getByText(/稍后重试一次/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: '重新检查' })).toHaveCount(0);
  await expect.poll(() => preflightRequests).toBe(1);
});

test('全局查询遇到确定性功能关闭时不自动重复请求', async ({ page }) => {
  const detail = workflow('waiting_input');
  let detailRequests = 0;
  await mockSharedApis(page, detail);
  await page.route('**/api/v1/workflows/81', async (route) => {
    detailRequests += 1;
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        error: {
          code: 'EXCEL_STAGE2_PIPELINE_DISABLED',
          message: 'Excel 第二阶段处理服务当前未启用。',
          details: {},
        },
        meta: { request_id: 'detail-disabled-r36' },
      }),
    });
  });

  await authenticate(page);
  await page.goto('/workflows/81');

  await expect(page.getByText('生产批次加载失败')).toBeVisible();
  await expect(page.getByRole('button', { name: '重试' })).toHaveCount(0);
  await expect.poll(() => detailRequests).toBe(1);
});

test('全局查询只对未知临时故障有限重试并可自动恢复', async ({ page }) => {
  const detail = workflow('waiting_input');
  let detailRequests = 0;
  await mockSharedApis(page, detail);
  await page.route('**/api/v1/workflows/81', async (route) => {
    detailRequests += 1;
    if (detailRequests < 3) {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'SERVICE_TEMPORARILY_UNAVAILABLE',
            message: '服务正在恢复，请稍候。',
            details: {},
          },
          meta: { request_id: `transient-r36-${detailRequests}` },
        }),
      });
      return;
    }
    await json(route, detail);
  });

  await authenticate(page);
  await page.goto('/workflows/81');

  await expect(page.getByRole('heading', { name: '生产批次 #81' })).toBeVisible();
  await expect.poll(() => detailRequests).toBe(3);
});

test('Excel 第二阶段读取表与正式结果使用两个单独下载入口', async ({ page }) => {
  const detail = workflow('succeeded');
  let readerDownloads = 0;
  let resultDownloads = 0;
  await mockSharedApis(page, detail);
  await page.route(
    '**/api/v1/workflows/81/stages/excel_stage2/download-reader-result',
    async (route) => {
      readerDownloads += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers: {
          'content-disposition': "attachment; filename*=UTF-8''BH%E5%B7%A6%E5%8F%B3%E8%BF%9B%E8%AF%BB%E5%8F%96%E8%A1%A8.xlsx",
          'access-control-expose-headers': 'content-disposition',
        },
        body: 'reader-xlsx',
      });
    },
  );
  await page.route(
    '**/api/v1/workflows/81/stages/excel_stage2/download-result',
    async (route) => {
      resultDownloads += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers: {
          'content-disposition': "attachment; filename*=UTF-8''%E6%9E%84%E4%BB%B6%E6%B8%85%E5%8D%95_BH%E5%B7%A6%E5%8F%B3%E8%BF%9B%E5%A4%84%E7%90%86%E5%90%8E.xlsx",
          'access-control-expose-headers': 'content-disposition',
        },
        body: 'stage2-xlsx',
      });
    },
  );

  await authenticate(page);
  await page.goto('/workflows/81');
  await page.getByRole('button', { name: /Excel 第二阶段处理/ }).click();

  await expect(page.getByRole('button', { name: '下载 BH 左右进读取表' })).toBeEnabled();
  await expect(page.getByRole('button', { name: '下载 Excel 第二阶段结果' })).toBeEnabled();
  const readerDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 BH 左右进读取表' }).click();
  expect((await readerDownload).suggestedFilename()).toBe('BH左右进读取表.xlsx');
  await expect.poll(() => readerDownloads).toBe(1);

  const resultDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 Excel 第二阶段结果' }).click();
  expect((await resultDownload).suggestedFilename()).toBe('构件清单_BH左右进处理后.xlsx');
  await expect.poll(() => resultDownloads).toBe(1);
});
