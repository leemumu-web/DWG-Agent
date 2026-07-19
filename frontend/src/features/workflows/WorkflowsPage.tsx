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
import { downloadFile, listBatches, listFilesPage } from '../../api/files.api';
import { listProjects } from '../../api/projects.api';
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
} from '../../api/workflows.api';
import { fmtDateTime, fmtSize, PageHeader, StatCard, StatGrid, StatusChip, type StatusStyle } from '../../components/ui';
import type { StoredFile } from '../../types/file';
import type {
  WorkflowDetail,
  WorkflowRun,
  WorkflowStageCapability,
} from '../../types/workflow';

const WORKFLOW_STATUS: Record<string, StatusStyle> = {
  draft: { color: '#667085', bg: '#f8fafc', border: '#e2e8f0', label: '草稿' },
  waiting_input: { color: '#7c3aed', bg: '#f5f3ff', border: '#ddd6fe', label: '待输入' },
  running: { color: '#2563eb', bg: '#eff6ff', border: '#bfdbfe', label: '进行中' },
  waiting_review: { color: '#d97706', bg: '#fffbeb', border: '#fde68a', label: '待确认' },
  succeeded: { color: '#059669', bg: '#ecfdf5', border: '#a7f3d0', label: '已完成' },
  failed: { color: '#dc2626', bg: '#fef2f2', border: '#fecaca', label: '失败' },
  cancelled: { color: '#667085', bg: '#f8fafc', border: '#e2e8f0', label: '已取消' },
};

const STAGE_STATUS: Record<string, 'wait' | 'process' | 'finish' | 'error'> = {
  pending: 'wait', ready: 'process', waiting_input: 'process', waiting_review: 'process',
  queued: 'process', running: 'process', succeeded: 'finish', failed: 'error', cancelled: 'error',
};

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);
const ACTIONABLE = new Set(['ready', 'waiting_input', 'waiting_review']);

function capabilityTag(capability?: WorkflowStageCapability) {
  if (!capability) return null;
  if (capability.implementation_status === 'implemented') {
    return <Tag color="success">服务器已实现</Tag>;
  }
  if (capability.implementation_status === 'external') {
    return <Tag color="processing">外部节点接口</Tag>;
  }
  return <Tag color="warning">核心接口留白</Tag>;
}

export function WorkflowsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<string>();
  const [createOpen, setCreateOpen] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [fileBatch, setFileBatch] = useState<string>();
  const [fileExt, setFileExt] = useState<string>();
  const [selectedFileId, setSelectedFileId] = useState<number>();
  const [executionBatch, setExecutionBatch] = useState<string>();
  const [form] = Form.useForm();

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
  const detail = detailQ.data as WorkflowDetail | undefined;
  const template = detail ? templateMap.get(detail.workflow_type as 'linux_production' | 'excel_delivery' | 'file_delivery') : undefined;
  const actionableStage = detail?.stages.find((stage) => ACTIONABLE.has(stage.status));
  const currentCapability = actionableStage
    ? template?.stages.find((stage) => stage.code === actionableStage.stage_code)
    : undefined;
  const currentArtifacts = detail && actionableStage
    ? detail.artifacts.filter((artifact) => artifact.stage_run_id === actionableStage.id)
    : [];
  const availableFiles = filesQ.data?.data ?? [];
  const fileMap = useMemo(() => new Map(availableFiles.map((file) => [file.id, file])), [availableFiles]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['workflows'] });
    if (detailId) queryClient.invalidateQueries({ queryKey: ['workflow', detailId] });
  };

  const createMutation = useMutation({
    mutationFn: createWorkflow,
    onSuccess: (created) => {
      message.success('流程已创建');
      setCreateOpen(false);
      form.resetFields();
      setDetailId(created.id);
      invalidate();
    },
    onError: (error: unknown) => message.error(error instanceof Error ? error.message : '创建失败'),
  });
  const startMutation = useMutation({
    mutationFn: startWorkflow,
    onSuccess: () => { message.success('流程已启动'); invalidate(); },
    onError: (error: unknown) => message.error(error instanceof Error ? error.message : '启动失败'),
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
        subtitle="统筹 Linux 文件、DXF→Excel、Excel Final 与 Windows CAM 交接；已实现能力真实执行，核心留白保持稳定接口"
        extra={<Space><Button icon={<ReloadOutlined />} loading={workflowsQ.isFetching} onClick={() => workflowsQ.refetch()}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建流程</Button></Space>}
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
      <Table className="surface-table" rowKey="id" dataSource={workflows} columns={columns} loading={workflowsQ.isLoading} scroll={{ x: 1050 }} pagination={{ current: page, pageSize, total: workflowsQ.data?.pagination.total ?? 0, showSizeChanger: true, onChange: (nextPage, nextSize) => { setPage(nextPage); setPageSize(nextSize); } }} locale={{ emptyText: <Empty description="暂无生产流程" /> }} />

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

            {actionableStage && currentCapability && !TERMINAL.has(detail.status) && (
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

      <Drawer title="新建生产流程" open={createOpen} onClose={() => setCreateOpen(false)} width={520} extra={<Button type="primary" loading={createMutation.isPending} onClick={() => form.submit()}>创建</Button>}>
        <Form form={form} layout="vertical" requiredMark={false} initialValues={{ workflow_type: 'linux_production' }} onFinish={(values) => createMutation.mutate(values)}>
          <Form.Item name="project_id" label="所属项目" rules={[{ required: true, message: '请选择项目' }]}><Select showSearch optionFilterProp="label" placeholder="选择项目" options={(projectsQ.data ?? []).map((project) => ({ value: project.id, label: `${project.code} · ${project.name}` }))} /></Form.Item>
          <Form.Item name="name" label="流程名称" rules={[{ required: true, message: '请输入流程名称' }, { max: 128 }]}><Input placeholder="如 2026-07 钢构生产批次" /></Form.Item>
          <Form.Item name="workflow_type" label="流程模板" rules={[{ required: true }]}><Select loading={templatesQ.isLoading} options={(templatesQ.data ?? []).map((item) => ({ value: item.code, label: item.name }))} /></Form.Item>
          <Alert type="info" showIcon message="Linux 生产模板" description="九阶段覆盖输入冻结、图纸处理、Excel 两阶段、CAM 交接、结果接纳与归档。DXF→Excel 和 Excel Final 直接调用现有服务器实现；CAD/CAM 核心能力保留可调用接口与产物契约。" />
        </Form>
      </Drawer>
    </>
  );
}
