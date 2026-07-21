import { useMemo, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  Popconfirm,
  Progress,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  ApartmentOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  CloudUploadOutlined,
  ClockCircleOutlined,
  DownloadOutlined,
  EyeOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { downloadFile, listBatches, listFilesPage } from '../files';
import { describeApiError } from '../../shared/api';
import { listProjects } from '../projects';
import {
  cancelWorkflow,
  completeWorkflowStage,
  createWorkflow,
  createWorkflowArtifact,
  executeWorkflowStage,
  getWorkflow,
  listWorkflows,
  listWorkflowTemplates,
  startWorkflow,
} from './workflows.api';
import { fmtDateTime, fmtSize, PageHeader, StatCard, StatGrid, StatusChip } from '../../shared/components';
import type { StoredFile } from '../files';
import type {
  WorkflowDetail,
  WorkflowRun,
} from './workflow';
import { ProductionInputPanel } from './ProductionInputPanel';
import { DxfClassificationPanel } from './DxfClassificationPanel';
import {
  ACTIONABLE,
  capabilityTag,
  STAGE_STATUS,
  suggestedBatchName,
  TERMINAL,
  WORKFLOW_STATUS,
} from './model/workflowPresentation';

export function WorkflowsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<string>();
  const [createOpen, setCreateOpen] = useState(false);
  const [submissionWorkflow, setSubmissionWorkflow] = useState<WorkflowDetail | null>(null);
  const [submissionStartError, setSubmissionStartError] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [fileBatch, setFileBatch] = useState<string>();
  const [fileExt, setFileExt] = useState<string>();
  const [selectedFileId, setSelectedFileId] = useState<number>();
  const [executionBatch, setExecutionBatch] = useState<string>();
  const [batchNameTouched, setBatchNameTouched] = useState(false);
  const [form] = Form.useForm();

  const openSubmission = () => {
    setSubmissionWorkflow(null);
    setSubmissionStartError(null);
    setBatchNameTouched(false);
    form.resetFields();
    setCreateOpen(true);
  };
  const closeSubmission = () => {
    setCreateOpen(false);
    setSubmissionWorkflow(null);
    setSubmissionStartError(null);
    setBatchNameTouched(false);
    form.resetFields();
  };

  const workflowsQ = useQuery({
    queryKey: ['workflows', page, pageSize, status],
    queryFn: () => listWorkflows({ page, page_size: pageSize, status }),
    refetchInterval: (query) => query.state.data?.data.some((item) => item.status === 'running') ? 4000 : false,
  });
  const projectsQ = useQuery({ queryKey: ['projects'], queryFn: listProjects });
  const templatesQ = useQuery({ queryKey: ['workflow-templates'], queryFn: listWorkflowTemplates });
  const detailQ = useQuery({
    queryKey: ['workflow', detailId],
    queryFn: () => getWorkflow(detailId!),
    enabled: detailId !== null,
    refetchInterval: (query) => query.state.data?.status === 'running' ? 2500 : false,
  });
  const batchesQ = useQuery({
    queryKey: ['workflow-file-batches', fileExt],
    queryFn: () => listBatches(fileExt),
    enabled: detailId !== null,
  });
  const filesQ = useQuery({
    queryKey: ['workflow-files', fileBatch, fileExt],
    queryFn: () => listFilesPage({
      page: 1,
      page_size: 100,
      batch_name: fileBatch,
      file_ext: fileExt,
      sort_by: 'created_at',
      sort_dir: 'desc',
    }),
    enabled: detailId !== null,
  });

  const projectMap = useMemo(() => new Map((projectsQ.data ?? []).map((project) => [project.id, project])), [projectsQ.data]);
  const templateMap = useMemo(() => new Map((templatesQ.data ?? []).map((template) => [template.code, template])), [templatesQ.data]);
  const workflows = workflowsQ.data?.data ?? [];
  const hasProjects = (projectsQ.data?.length ?? 0) > 0;
  const detail = detailQ.data as WorkflowDetail | undefined;
  const template = detail ? templateMap.get(detail.workflow_type as 'linux_production' | 'excel_delivery' | 'file_delivery') : undefined;
  const actionableStage = detail?.stages.find((stage) => ACTIONABLE.has(stage.status));
  const classificationStage = detail?.stages.find((stage) => stage.stage_code === 'dxf_classification');
  const currentCapability = actionableStage
    ? template?.stages.find((stage) => stage.code === actionableStage.stage_code)
    : undefined;
  const currentArtifacts = detail && actionableStage
    ? detail.artifacts.filter((artifact) => artifact.stage_run_id === actionableStage.id)
    : [];
  const availableFiles = filesQ.data?.data ?? [];
  const fileMap = useMemo(() => new Map(availableFiles.map((file) => [file.id, file])), [availableFiles]);
  const selectSubmissionProject = (projectId: number | undefined) => {
    const project = projectId ? projectMap.get(projectId) : undefined;
    if (!batchNameTouched) form.setFieldValue('name', project ? suggestedBatchName(project.code) : undefined);
  };

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['workflows'] });
    if (detailId) queryClient.invalidateQueries({ queryKey: ['workflow', detailId] });
  };
  const refreshSubmission = async () => {
    invalidate();
    if (!submissionWorkflow) return;
    try {
      setSubmissionWorkflow(await getWorkflow(submissionWorkflow.id));
    } catch (error) {
      message.error(describeApiError(error, '批次状态刷新失败'));
    }
  };

  const createMutation = useMutation({
    mutationFn: async (values: { project_id: number; name: string }) => {
      const created = await createWorkflow({
        ...values,
        workflow_type: 'linux_production',
      });
      try {
        const started = await startWorkflow(created.id);
        return { workflow: started, startError: null as unknown };
      } catch (startError) {
        return { workflow: created, startError };
      }
    },
    onSuccess: ({ workflow, startError }) => {
      form.resetFields();
      setBatchNameTouched(false);
      setSubmissionWorkflow(workflow);
      setSubmissionStartError(startError ? describeApiError(startError, '启动失败') : null);
      invalidate();
      if (startError) {
        message.warning(`批次已创建，但启动失败：${describeApiError(startError, '请重试启动')}`);
      } else {
        message.success('生产批次已创建并启动，请上传 DWG 和 Excel');
      }
    },
    onError: (error: unknown) => message.error(describeApiError(error, '生产批次创建失败')),
  });
  const startMutation = useMutation({
    mutationFn: startWorkflow,
    onSuccess: (started) => {
      if (submissionWorkflow?.id === started.id) {
        setSubmissionWorkflow(started);
        setSubmissionStartError(null);
      }
      message.success('流程已启动，可以提交生产资料');
      invalidate();
    },
    onError: (error: unknown) => {
      const text = describeApiError(error, '启动失败');
      if (submissionWorkflow) setSubmissionStartError(text);
      message.error(text);
    },
  });
  const completeMutation = useMutation({
    mutationFn: ({ id, stage }: { id: number; stage: string }) => completeWorkflowStage(id, stage),
    onSuccess: () => { message.success('阶段已确认'); invalidate(); },
    onError: (error: unknown) => message.error(error instanceof Error ? error.message : '确认失败'),
  });
  const cancelMutation = useMutation({
    mutationFn: cancelWorkflow,
    onSuccess: () => { message.success('流程及当前任务已取消'); invalidate(); },
    onError: (error: unknown) => message.error(error instanceof Error ? error.message : '取消失败'),
  });
  const artifactMutation = useMutation({
    mutationFn: ({ workflowId, file }: { workflowId: number; file: StoredFile }) => {
      if (!actionableStage) throw new Error('当前没有可绑定文件的阶段');
      const artifactType = currentCapability?.artifact_types[0] ?? 'workflow_file';
      return createWorkflowArtifact(workflowId, {
        stage_code: actionableStage.stage_code,
        artifact_type: artifactType,
        file_id: file.id,
        metadata: { original_name: file.original_name, sha256: file.sha256 },
      });
    },
    onSuccess: (data) => { message.success(data.reused ? '文件已绑定，无需重复登记' : '文件已绑定到当前阶段'); invalidate(); },
    onError: (error: unknown) => message.error(error instanceof Error ? error.message : '文件绑定失败'),
  });
  const executeMutation = useMutation({
    mutationFn: () => {
      if (!detail || !actionableStage || !currentCapability?.execution_kind) {
        throw new Error('当前阶段没有执行接口');
      }
      return executeWorkflowStage(detail.id, actionableStage.stage_code, {
        execution_kind: currentCapability.execution_kind,
        ...(currentCapability.execution_kind === 'dxf_to_excel' ? { batch_name: executionBatch } : {}),
        ...(currentCapability.execution_kind === 'excel_final' ? { file_id: selectedFileId } : {}),
      });
    },
    onSuccess: (data) => { message.success(data.retried ? `任务 #${data.job.id} 已重新入队（尝试 #${data.job.attempt}）` : data.reused ? `继续跟踪任务 #${data.job.id}` : `任务 #${data.job.id} 已提交`); invalidate(); },
    onError: (error: unknown) => message.error(error instanceof Error ? error.message : '阶段执行失败'),
  });

  const runningCount = workflows.filter((item) => item.status === 'running').length;
  const waitingCount = workflows.filter((item) => item.status === 'waiting_input' || item.status === 'waiting_review').length;
  const completedCount = workflows.filter((item) => item.status === 'succeeded').length;
  const canConfirm = Boolean(
    detail && actionableStage && currentCapability
    && actionableStage.stage_code !== 'source_intake'
    && currentCapability.execution_mode !== 'automated'
    && (currentCapability.execution_mode === 'manual' || currentArtifacts.length > 0),
  );

  const columns = [
    {
      title: '流程', dataIndex: 'name',
      render: (name: string, record: WorkflowRun) => (
        <div><Typography.Text strong>{name}</Typography.Text><div><Typography.Text type="secondary" style={{ fontSize: 12 }}>#{record.id} · {templateMap.get(record.workflow_type as 'linux_production')?.name ?? record.workflow_type}</Typography.Text></div></div>
      ),
    },
    { title: '项目', dataIndex: 'project_id', width: 170, render: (id: number) => projectMap.get(id)?.name ?? `#${id}` },
    { title: '状态', dataIndex: 'status', width: 120, render: (value: string) => <StatusChip style={WORKFLOW_STATUS[value] ?? WORKFLOW_STATUS.draft} /> },
    { title: '当前阶段', dataIndex: 'current_stage', width: 170, render: (value?: string | null) => value ? <Tag>{value}</Tag> : '—' },
    { title: '进度', dataIndex: 'progress', width: 190, render: (value: number) => <Progress percent={value} size="small" /> },
    { title: '更新时间', dataIndex: 'updated_at', width: 170, render: (value: string) => <Typography.Text type="secondary">{fmtDateTime(value)}</Typography.Text> },
    { title: '操作', width: 90, align: 'right' as const, render: (_: unknown, record: WorkflowRun) => <Button type="text" icon={<EyeOutlined />} onClick={() => setDetailId(record.id)}>详情</Button> },
  ];

  return (
    <>
      <PageHeader
        title="生产流程"
        subtitle="从多份 DWG 与一份 Excel 开始生产批次；服务器生成并冻结 DXF，随后按标题栏信息预处理和分类分流"
        extra={<Space><Button icon={<ReloadOutlined />} loading={workflowsQ.isFetching} onClick={() => workflowsQ.refetch()}>刷新</Button><Button type="primary" size="large" icon={<PlusOutlined />} onClick={openSubmission}>新建并上传生产批次</Button></Space>}
      />
      <StatGrid>
        <StatCard label="当前页流程" value={workflows.length} icon={<ApartmentOutlined />} color="#2563eb" bg="#eff6ff" />
        <StatCard label="进行中" value={runningCount} icon={<ReloadOutlined spin={runningCount > 0} />} color="#2563eb" bg="#eff6ff" />
        <StatCard label="待操作" value={waitingCount} icon={<ClockCircleOutlined />} color="#d97706" bg="#fffbeb" />
        <StatCard label="已完成" value={completedCount} icon={<CheckCircleOutlined />} color="#059669" bg="#ecfdf5" />
      </StatGrid>
      <div className="table-toolbar">
        <Select allowClear placeholder="筛选状态" value={status} onChange={(value) => { setStatus(value); setPage(1); }} style={{ width: 180 }} options={Object.entries(WORKFLOW_STATUS).map(([value, meta]) => ({ value, label: meta.label }))} />
        <Typography.Text type="secondary">共 {workflowsQ.data?.pagination.total ?? 0} 条流程</Typography.Text>
      </div>
      <Table className="surface-table" rowKey="id" dataSource={workflows} columns={columns} loading={workflowsQ.isLoading} scroll={{ x: 1050 }} pagination={{ current: page, pageSize, total: workflowsQ.data?.pagination.total ?? 0, showSizeChanger: true, onChange: (nextPage, nextSize) => { setPage(nextPage); setPageSize(nextSize); } }} locale={{ emptyText: <Empty description={<Space orientation="vertical"><span>还没有生产批次</span><Typography.Text type="secondary">从项目、批次名和生产资料开始，DXF 由服务器生成。</Typography.Text><Button type="primary" icon={<CloudUploadOutlined />} onClick={openSubmission}>新建并上传第一批资料</Button></Space>} /> }} />

      <Drawer title={detail ? `流程 #${detail.id} · ${detail.name}` : '流程详情'} open={detailId !== null} onClose={() => setDetailId(null)} width="min(920px, 96vw)" loading={detailQ.isLoading} extra={detail && <Space>{detail.status === 'draft' && <Button type="primary" loading={startMutation.isPending} onClick={() => startMutation.mutate(detail.id)}>启动</Button>}{canConfirm && detail.status !== 'draft' && actionableStage && <Button type="primary" loading={completeMutation.isPending} onClick={() => completeMutation.mutate({ id: detail.id, stage: actionableStage.stage_code })}>确认当前阶段</Button>}{!TERMINAL.has(detail.status) && <Popconfirm title="确定取消流程及当前活动任务？" onConfirm={() => cancelMutation.mutate(detail.id)}><Button danger icon={<StopOutlined />}>取消</Button></Popconfirm>}</Space>}>
        {detail && (
          <>
            <Descriptions className="detail-descriptions" bordered column={2} size="small" items={[
              { key: 'project', label: '项目', children: projectMap.get(detail.project_id)?.name ?? `#${detail.project_id}` },
              { key: 'type', label: '流程模板', children: template?.name ?? detail.workflow_type },
              { key: 'status', label: '状态', children: <StatusChip style={WORKFLOW_STATUS[detail.status] ?? WORKFLOW_STATUS.draft} /> },
              { key: 'progress', label: '整体进度', children: <Progress percent={detail.progress} size="small" /> },
            ]} />
            <Typography.Title level={5} style={{ marginTop: 26 }}>生产轨道</Typography.Title>
            <Steps direction="vertical" current={Math.max(0, detail.stages.findIndex((stage) => !['succeeded', 'skipped'].includes(stage.status)))} items={detail.stages.map((stage) => {
              const capability = template?.stages.find((item) => item.code === stage.stage_code);
              return {
                title: <Space wrap>{stage.name}{capabilityTag(capability)}</Space>,
                status: STAGE_STATUS[stage.status] ?? 'wait',
                description: <Space orientation="vertical" size={2}><Typography.Text type="secondary">{stage.stage_code} · {WORKFLOW_STATUS[stage.status]?.label ?? stage.status}</Typography.Text>{capability && <Typography.Text type="secondary">{capability.description}</Typography.Text>}{stage.job_id && <Typography.Link href="/jobs">任务 #{stage.job_id} · 尝试 #{stage.job_attempt}</Typography.Link>}{stage.error_message && <Typography.Text type="danger">{stage.error_message}</Typography.Text>}</Space>,
              };
            })} />

            {detail.workflow_type === 'linux_production' && detail.status === 'draft' && (
              <Card title={<Space><CloudUploadOutlined />01 · 提交生产资料</Space>} style={{ marginTop: 12 }}>
                <Alert
                  type="info"
                  showIcon
                  message="批次已创建，启动后即可上传"
                  description="下一步上传多个 DWG 和恰好一个 Excel；DXF 将由服务器生成，不需要人工准备。"
                  action={<Button type="primary" loading={startMutation.isPending} onClick={() => startMutation.mutate(detail.id)}>启动并进入上传</Button>}
                />
              </Card>
            )}

            {detail.workflow_type === 'linux_production' && detail.status !== 'draft' && (
              <>
                <ProductionInputPanel
                  workflowId={detail.id}
                  sourceIntakeActive={Boolean(actionableStage && actionableStage.stage_code === 'source_intake')}
                  onFrozen={invalidate}
                />
                <DxfClassificationPanel
                  workflowId={detail.id}
                  stage={classificationStage}
                  isCurrent={detail.current_stage === 'dxf_classification'}
                  onChanged={invalidate}
                />
              </>
            )}

            {actionableStage && currentCapability && !['source_intake', 'dxf_classification'].includes(actionableStage.stage_code) && !TERMINAL.has(detail.status) && (
              <Card title={<Space><CloudServerOutlined />当前阶段控制台{capabilityTag(currentCapability)}</Space>} style={{ marginTop: 12 }}>
                <Alert type={currentCapability.implementation_status === 'implemented' ? 'info' : 'warning'} showIcon message={currentCapability.description} description={currentCapability.implementation_status === 'implemented' ? `执行方式：${currentCapability.execution_mode}` : `接口已预留：${currentCapability.execution_kind}；需输入 ${currentCapability.required_inputs.join('、') || '无'}；产物 ${currentCapability.artifact_types.join('、') || '待定义'}`} />
                {currentCapability.execution_mode === 'automated' && (
                  <Space wrap style={{ marginTop: 16 }}>
                    {currentCapability.execution_kind === 'dxf_to_excel' && <Select aria-label="DXF 批次" showSearch placeholder="选择 DXF 批次" value={executionBatch} onChange={setExecutionBatch} style={{ width: 300 }} options={(batchesQ.data ?? []).map((batch) => ({ value: batch.name, label: `${batch.name} · ${batch.file_count} 文件` }))} />}
                    {currentCapability.execution_kind === 'excel_final' && <Select aria-label="Excel 输入文件" showSearch optionFilterProp="label" placeholder="选择 Excel 文件" value={selectedFileId} onChange={setSelectedFileId} style={{ width: 360 }} options={availableFiles.filter((file) => ['.xls', '.xlsx'].includes(file.file_ext)).map((file) => ({ value: file.id, label: `#${file.id} · ${file.original_name}` }))} />}
                    <Button type="primary" icon={<ThunderboltOutlined />} loading={executeMutation.isPending} disabled={currentCapability.execution_kind === 'dxf_to_excel' ? !executionBatch : !selectedFileId} onClick={() => executeMutation.mutate()}>执行服务器阶段</Button>
                  </Space>
                )}
                {['placeholder', 'external'].includes(currentCapability.execution_mode) && (
                  <Button style={{ marginTop: 16 }} icon={<ThunderboltOutlined />} loading={executeMutation.isPending} onClick={() => executeMutation.mutate()}>验证占位接口</Button>
                )}
                <Divider>文件管理与阶段产物</Divider>
                <Space wrap>
                  <Select allowClear placeholder="按扩展名" value={fileExt} onChange={setFileExt} style={{ width: 150 }} options={['.dwg', '.dxf', '.xls', '.xlsx'].map((value) => ({ value, label: value.toUpperCase() }))} />
                  <Select allowClear showSearch placeholder="按批次" value={fileBatch} onChange={setFileBatch} style={{ width: 240 }} options={(batchesQ.data ?? []).map((batch) => ({ value: batch.name, label: `${batch.name} · ${batch.file_count}` }))} />
                  <Select aria-label="待绑定文件" showSearch optionFilterProp="label" placeholder="选择文件" value={selectedFileId} onChange={setSelectedFileId} style={{ width: 360 }} loading={filesQ.isFetching} options={availableFiles.map((file) => ({ value: file.id, label: `#${file.id} · ${file.original_name} · ${fmtSize(file.size_bytes)}` }))} />
                  <Button icon={<LinkOutlined />} disabled={!selectedFileId} loading={artifactMutation.isPending} onClick={() => { const file = fileMap.get(selectedFileId!); if (file) artifactMutation.mutate({ workflowId: detail.id, file }); }}>绑定到当前阶段</Button>
                </Space>
                <div style={{ marginTop: 16 }}>
                  {currentArtifacts.length ? currentArtifacts.map((artifact) => {
                    const file = artifact.file_id ? fileMap.get(artifact.file_id) : undefined;
                    return <Tag key={artifact.id} closable={false} style={{ marginBottom: 8 }}>{artifact.artifact_type} · v{artifact.version}{artifact.file_id ? ` · 文件 #${artifact.file_id}` : ''}{artifact.file_id && <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => downloadFile(artifact.file_id!, file?.original_name ?? `workflow-artifact-${artifact.file_id}`)}>下载</Button>}</Tag>;
                  }) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前阶段暂无产物" />}
                </div>
              </Card>
            )}
          </>
        )}
      </Drawer>

      <Drawer
        title={submissionWorkflow ? `生产批次 #${submissionWorkflow.id} · 资料提交` : '新建生产批次'}
        open={createOpen}
        onClose={closeSubmission}
        width={submissionWorkflow ? 'min(1120px, 96vw)' : 520}
        closable={!createMutation.isPending}
        maskClosable={!createMutation.isPending}
        keyboard={!createMutation.isPending}
      >
        {submissionWorkflow ? (
          submissionWorkflow.status === 'draft' ? (
            <Alert
              type="warning"
              showIcon
              message="批次已创建，等待启动"
              description={submissionStartError ?? '启动后即可在当前窗口上传多个 DWG 和一个 Excel。'}
              action={<Button type="primary" loading={startMutation.isPending} onClick={() => startMutation.mutate(submissionWorkflow.id)}>重试启动并进入上传</Button>}
            />
          ) : (
            <>
              <ProductionInputPanel
                workflowId={submissionWorkflow.id}
                sourceIntakeActive={submissionWorkflow.current_stage === 'source_intake'}
                onFrozen={refreshSubmission}
              />
              <DxfClassificationPanel
                workflowId={submissionWorkflow.id}
                stage={submissionWorkflow.stages.find((stage) => stage.stage_code === 'dxf_classification')}
                isCurrent={submissionWorkflow.current_stage === 'dxf_classification'}
                onChanged={refreshSubmission}
              />
            </>
          )
        ) : (
          <Form className="production-create-form" form={form} layout="vertical" requiredMark={false} onFinish={(values) => createMutation.mutate(values)}>
            <section className="production-create-hero" aria-label="生产批次说明">
              <Typography.Text className="production-create-eyebrow">生产资料入口</Typography.Text>
              <Typography.Title level={4}>先建批次，再在当前窗口上传资料</Typography.Title>
              <Typography.Paragraph>上传多个 DWG 与恰好一个 Excel；系统会完成 DXF 转换、配对、冻结和后续分类分流。</Typography.Paragraph>
            </section>
            <Steps className="production-create-steps" size="small" current={0} responsive items={[{ title: '选择项目' }, { title: '确认批次名' }, { title: '进入资料上传' }]} />
            <div className="production-create-checklist" aria-label="文件准备清单">
              <span><CheckCircleOutlined /> 多个 DWG + 1 个 Excel</span>
              <span><CloudServerOutlined /> 无需准备 DXF</span>
            </div>
            <Form.Item name="project_id" label="所属项目" rules={[{ required: true, message: '请选择项目' }]} extra="批次将继承项目权限和文件归属。">
              <Select showSearch optionFilterProp="label" disabled={createMutation.isPending} placeholder="选择本次生产资料所属项目" onChange={selectSubmissionProject} options={(projectsQ.data ?? []).map((project) => ({ value: project.id, label: `${project.code} · ${project.name}` }))} />
            </Form.Item>
            <Form.Item name="name" label="批次名称" rules={[{ required: true, message: '请输入批次名称' }, { max: 128 }]} extra="已按项目和日期生成建议名称，可按现场批次规则修改。">
              <Input disabled={createMutation.isPending} placeholder="例如 P001-20260719-生产批次" onChange={() => setBatchNameTouched(true)} />
            </Form.Item>
            {hasProjects ? (
              <div className="production-create-actions">
                <Typography.Text type="secondary">本步不会上传文件；创建成功后直接进入资料上传。</Typography.Text>
                <Space className="production-create-action-buttons" wrap>
                  <Button disabled={createMutation.isPending} onClick={closeSubmission}>取消</Button>
                  <Button htmlType="submit" type="primary" icon={<CloudUploadOutlined />} loading={createMutation.isPending}>创建批次，下一步上传文件</Button>
                </Space>
              </div>
            ) : <Alert type="warning" showIcon message="需要先创建项目" description="生产批次必须归属一个项目，当前没有可选项目。" action={<Button href="/projects">先创建项目</Button>} />}
          </Form>
        )}
      </Drawer>
    </>
  );
}
