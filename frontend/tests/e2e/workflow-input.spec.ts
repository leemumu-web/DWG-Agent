import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-07-19T10:00:00Z';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'workflow-input-e2e', timestamp: now } });
const user = {
  id: 1, username: 'operator', real_name: '生产操作员', status: 'active',
  roles: [{ id: 1, code: 'super_admin', name: '管理员', is_system: true }],
  created_at: now, updated_at: now,
};

function storedFile(id: number, name: string) {
  const ext = name.slice(name.lastIndexOf('.')).toLowerCase();
  return {
    id, bucket: 'dwg-original', storage_key: `e2e/${id}${ext}`, original_name: name,
    file_ext: ext, content_type: 'application/octet-stream', size_bytes: 2054,
    sha256: String(id).padStart(64, '0'), batch_name: 'workflow-input-501',
    uploaded_by: 1, status: 'available', created_at: now, updated_at: now,
  };
}

async function json(route: Route, data: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(envelope(data)) });
}

async function mockWorkflow(page: Page) {
  const workflow = {
    id: 41, project_id: 7, created_by: 1, name: '7 月生产输入', workflow_type: 'linux_production',
    status: 'waiting_input', current_stage: 'source_intake', progress: 0, config_json: {},
    error_code: null, error_message: null, started_at: now, finished_at: null,
    created_at: now, updated_at: now,
  };
  const stages = [{
    id: 91, stage_code: 'source_intake', name: '文件上传、完整性校验与输入冻结', sequence: 1,
    status: 'waiting_input', job_id: null, job_attempt: null, progress: 0,
    input_json: null, output_json: null, error_code: null, error_message: null,
    started_at: now, finished_at: null, created_at: now, updated_at: now,
  }, {
    id: 92, stage_code: 'dxf_classification', name: 'DXF 分类与分流', sequence: 2,
    status: 'pending', job_id: null, job_attempt: null, progress: 0,
    input_json: null, output_json: null, error_code: null, error_message: null,
    started_at: null, finished_at: null, created_at: now, updated_at: now,
  }];
  const template = {
    code: 'linux_production', name: 'Linux 生产流程', description: '生产输入',
    stages: [{
      code: 'source_intake', name: stages[0].name,
      description: '上传多个 DWG 和一个 Excel，由服务器生成 DXF',
      execution_mode: 'manual', implementation_status: 'implemented', execution_kind: null,
      required_inputs: ['dwg_files', 'excel_file'], artifact_types: ['source_file', 'source_excel', 'derived_dxf'],
    }, {
      code: 'dxf_classification', name: 'DXF 分类与分流',
      description: '调用 Steel DXF Classifier 1.1.0 预处理并按零件类型分流冻结 DXF',
      execution_mode: 'automated', implementation_status: 'implemented', execution_kind: 'steel_dxf_classification',
      required_inputs: ['frozen_derived_dxf'], artifact_types: ['classified_dxf', 'classification_report', 'classification_manifest'],
    }],
  };
  let items: Array<Record<string, unknown>> = [];
  let frozen = false;
  let classificationStarted = false;
  let nextFileId = 700;
  const batch = () => ({
    id: 501, workflow_run_id: 41, project_id: 7, status: frozen ? 'frozen' : items.some((item) => item.derived_dxf) ? 'ready_to_freeze' : 'uploading',
    version: frozen ? 1 : 0, manifest_sha256: frozen ? 'a'.repeat(64) : null, frozen_at: frozen ? now : null,
    counts: {
      dwg: items.filter((item) => item.role === 'source_dwg').length,
      excel: items.filter((item) => item.role === 'source_excel').length,
      paired: items.filter((item) => item.derived_dxf).length,
      converting: 0, failed: 0,
    },
    items, issues: [], freeze_ready: items.some((item) => item.role === 'source_dwg') && items.some((item) => item.role === 'source_excel') && items.filter((item) => item.role === 'source_dwg').every((item) => item.derived_dxf),
    created_at: now, updated_at: now,
  });

  await page.route('**/api/v1/auth/tokens/refresh', (route) => json(route, { access_token: 'e2e-token', user }, 201));
  await page.route('**/api/v1/projects?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      ...envelope([{ id: 7, code: 'P7', name: '生产项目' }]),
      pagination: { page: 1, page_size: 200, total: 1, total_pages: 1 },
    }),
  }));
  await page.route('**/api/v1/workflows/templates', (route) => json(route, [template]));
  await page.route('**/api/v1/workflows?**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...envelope([workflow]), pagination: { page: 1, page_size: 20, total: 1, total_pages: 1 } }) }));
  await page.route('**/api/v1/workflows', (route) => json(route, { ...workflow, status: 'draft', started_at: null, stages, artifacts: [] }, 201));
  const workflowDetail = () => ({
    ...workflow,
    current_stage: frozen ? 'dxf_classification' : 'source_intake',
    status: classificationStarted ? 'running' : 'waiting_input',
    stages: stages.map((stage) => stage.stage_code === 'source_intake'
      ? { ...stage, status: frozen ? 'succeeded' : 'waiting_input', progress: frozen ? 100 : 0 }
      : { ...stage, status: classificationStarted ? 'running' : frozen ? 'waiting_input' : 'pending', job_id: classificationStarted ? 990 : null, job_attempt: classificationStarted ? 1 : null, progress: classificationStarted ? 35 : 0 }),
    artifacts: [],
  });
  await page.route('**/api/v1/workflows/41/start', (route) => json(route, workflowDetail()));
  await page.route('**/api/v1/workflows/41/stages/dxf_classification/executions', async (route) => {
    classificationStarted = true;
    await json(route, {
      workflow: workflowDetail(),
      job: { id: 990, project_id: 7, drawing_id: null, created_by: 1, task_type: 'classify_steel_dxf', precision_level: 'normal', pipeline: 'steel_dxf_classifier', status: 'queued', attempt: 1, priority: 0, progress: 0, params_json: { workflow_id: 41 }, error_code: null, error_message: null, progress_data: null, created_at: now, updated_at: now, started_at: null, finished_at: null },
      reused: false,
      retried: false,
    }, 202);
  });
  await page.route('**/api/v1/workflows/41/dxf-classification', (route) => json(route, classificationStarted ? {
    id: 77, workflow_run_id: 41, status: 'completed', classifier_version: '1.1.0',
    report_schema: 'STEEL-DXF-CLASSIFICATION-1.1', cli_schema: 'STEEL-DXF-CLI-1.1',
    project_name: 'P7-workflow-41', input_manifest_sha256: 'a'.repeat(64), input_count: 1,
    classified_count: 1, review_required_count: 0, unreadable_count: 0, type_counts: { BH: 1 },
    report_file: storedFile(910, 'P7-workflow-41_分类报告.json'),
    manifest_file: storedFile(911, 'P7-workflow-41_分类清单.csv'),
    job: { id: 990, project_id: 7, drawing_id: null, created_by: 1, task_type: 'classify_steel_dxf', precision_level: 'normal', pipeline: 'steel_dxf_classifier', status: 'succeeded', attempt: 1, priority: 0, progress: 100, params_json: { workflow_id: 41 }, error_code: null, error_message: null, progress_data: null, created_at: now, updated_at: now, started_at: now, finished_at: now },
    items: [{ id: 88, drawing_id: 1000, source_file: storedFile(900, 'panel-A.dxf'), output_file: storedFile(912, 'panel-A_拆板前.dxf'), source_name: 'panel-A.dxf', output_name: 'panel-A_拆板前.dxf', output_directory: 'P7-workflow-41_BH_dxf', disposition: 'classified', part_type: 'BH', diagnostics: [] }],
    error_code: null, error_message: null, started_at: now, finished_at: now, created_at: now, updated_at: now,
  } : null));
  await page.route('**/api/v1/workflows/41', (route) => json(route, workflowDetail()));
  await page.route('**/api/v1/files/batches**', (route) => json(route, []));
  await page.route('**/api/v1/files?**', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...envelope([]), pagination: { page: 1, page_size: 100, total: 0, total_pages: 1 } }) });
      return;
    }
    nextFileId += 1;
    const name = nextFileId === 701 ? 'panel-A.dwg' : 'parts.xlsx';
    await json(route, storedFile(nextFileId, name), 201);
  });
  await page.route('**/api/v1/workflows/41/input-batch/conversion-requests', async (route) => {
    items = items.map((item) => item.role === 'source_dwg' ? {
      ...item,
      status: 'paired',
      conversion_job: { id: 801, task_type: 'convert_dwg_to_dxf', precision_level: 'normal', status: 'succeeded', attempt: 1, priority: 0, progress: 100, created_at: now, updated_at: now },
      derived_dxf: storedFile(900, String(item.original_name).replace(/\.dwg$/i, '.dxf')),
    } : item);
    await json(route, { batch: batch(), jobs: [], dispatched_count: 1 }, 202);
  });
  await page.route('**/api/v1/workflows/41/input-batch/freeze', async (route) => {
    frozen = true;
    items = items.map((item, index) => item.role === 'source_dwg' ? { ...item, status: 'frozen', drawing_id: 1000 + index } : { ...item, status: 'frozen' });
    await json(route, batch());
  });
  await page.route('**/api/v1/workflows/41/input-batch/files', async (route) => {
    const fileId = Number((route.request().postDataJSON() as { file_id: number }).file_id);
    const file = fileId === 701 ? storedFile(fileId, 'panel-A.dwg') : storedFile(fileId, 'parts.xlsx');
    const item = {
      id: 600 + fileId, role: file.file_ext === '.dwg' ? 'source_dwg' : 'source_excel', status: 'validated',
      original_name: file.original_name, normalized_stem: file.original_name.replace(/\.[^.]+$/, '').toLowerCase(),
      file, conversion_job: null, derived_dxf: null, drawing_id: null, error_code: null, error_message: null,
    };
    items = [...items, item];
    await json(route, { batch: batch(), item_id: item.id, reused: false }, 201);
  });
  await page.route('**/api/v1/workflows/41/input-batch', (route) => json(route, batch(), route.request().method() === 'POST' ? 201 : 200));
}

test('production source intake prevents DXF mistakes and freezes server-generated pairs', async ({ page }) => {
  await mockWorkflow(page);
  await page.goto('/');
  await page.evaluate(({ token, savedUser }) => {
    sessionStorage.setItem('dwg_access_token', token);
    sessionStorage.setItem('dwg_user', JSON.stringify(savedUser));
  }, { token: 'e2e-token', savedUser: user });
  await page.goto('/workflows');
  await expect(page.getByRole('button', { name: /新建并上传生产批次/ })).toBeVisible();
  await page.getByRole('button', { name: /新建并上传生产批次/ }).click();
  const submission = page.getByRole('dialog', { name: '新建生产批次' });
  await expect(submission.getByText('多个 DWG + 1 个 Excel')).toBeVisible();
  await expect(submission.getByText('进入资料上传', { exact: true })).toBeVisible();
  await submission.getByRole('combobox', { name: '所属项目' }).click();
  await page.getByText('P7 · 生产项目').click();
  await expect(submission.getByRole('textbox', { name: '批次名称' })).toHaveValue('P7-20260719-生产批次');
  await submission.getByRole('textbox', { name: '批次名称' }).fill('浏览器生产批次');
  await expect(submission.getByText('本步不会上传文件')).toBeVisible();
  await submission.getByRole('button', { name: '创建批次，下一步上传文件' }).click();
  const uploadStep = page.getByRole('dialog', { name: '生产批次 #41 · 资料提交' });
  await expect(uploadStep.getByRole('button', { name: /上传 DWG/ })).toBeVisible();
  await expect(uploadStep.getByRole('button', { name: '上传 Excel' })).toBeVisible();
  await uploadStep.getByRole('button', { name: '关闭' }).click();
  await page.getByRole('button', { name: '详情' }).click();
  await expect(page.getByText('只需上传多个 DWG 和一个 Excel')).toBeVisible();

  let chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: /上传 DWG/ }).click();
  await (await chooser).setFiles({ name: 'manual.dxf', mimeType: 'application/dxf', buffer: Buffer.from('0\nEOF\n') });
  await expect(page.getByText(/INPUT_DXF_NOT_ALLOWED/)).toBeVisible();

  chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: /上传 DWG/ }).click();
  await (await chooser).setFiles({ name: 'panel-A.dwg', mimeType: 'application/acad', buffer: Buffer.concat([Buffer.from('AC1027'), Buffer.alloc(2048)]) });
  await expect(page.getByText('panel-A.dwg', { exact: true })).toBeVisible();

  chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: '上传 Excel' }).click();
  await (await chooser).setFiles({ name: 'parts.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('xlsx') });
  await expect(page.getByRole('button', { name: '上传 Excel' })).toBeDisabled();

  await page.getByRole('button', { name: '生成并校验 DXF' }).click();
  await expect(page.getByText('已配对')).toBeVisible();
  await expect(page.getByText('完整性检查通过，可以冻结')).toBeVisible();
  await page.getByRole('button', { name: '冻结输入版本' }).click();
  await expect(page.getByText(/冻结后不可修改/)).toBeVisible();
  await page.getByRole('button', { name: '确认冻结' }).click();
  await expect(page.getByText('输入版本已冻结')).toBeVisible();
  await expect(page.getByText(`版本 v1 · 清单 ${'a'.repeat(64)}`)).toBeVisible();
  await expect(page.getByRole('button', { name: '开始 DXF 分类分流' })).toBeVisible();
  await page.getByRole('button', { name: '开始 DXF 分类分流' }).click();
  await expect(page.getByText('全部 DXF 已完成分类分流')).toBeVisible();
  await expect(page.getByText('P7-workflow-41_BH_dxf')).toBeVisible();
});
