import { useMemo, useState } from 'react';
import {
  App,
  Button,
  Descriptions,
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
  ClockCircleOutlined,
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listProjects } from '../../api/projects.api';
import {
  cancelWorkflow,
  completeWorkflowStage,
  createWorkflow,
  getWorkflow,
  listWorkflows,
  startWorkflow,
} from '../../api/workflows.api';
import { fmtDateTime, PageHeader, StatCard, StatGrid, StatusChip, type StatusStyle } from '../../components/ui';
import type { WorkflowDetail, WorkflowRun } from '../../types/workflow';

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

export function WorkflowsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<string>();
  const [createOpen, setCreateOpen] = useState(false);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [form] = Form.useForm();

  const workflowsQ = useQuery({
    queryKey: ['workflows', page, pageSize, status],
    queryFn: () => listWorkflows({ page, page_size: pageSize, status }),
    refetchInterval: (query) => query.state.data?.data.some((item) => item.status === 'running') ? 4000 : false,
  });
  const projectsQ = useQuery({ queryKey: ['projects'], queryFn: listProjects });
  const detailQ = useQuery({
    queryKey: ['workflow', detailId],
    queryFn: () => getWorkflow(detailId!),
    enabled: detailId !== null,
    refetchInterval: (query) => query.state.data?.status === 'running' ? 2500 : false,
  });

  const projectMap = useMemo(() => new Map((projectsQ.data ?? []).map((project) => [project.id, project])), [projectsQ.data]);
  const workflows = workflowsQ.data?.data ?? [];
  const runningCount = workflows.filter((item) => item.status === 'running').length;
  const waitingCount = workflows.filter((item) => item.status === 'waiting_input' || item.status === 'waiting_review').length;
  const completedCount = workflows.filter((item) => item.status === 'succeeded').length;

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
    onSuccess: () => { message.success('流程已取消'); invalidate(); },
    onError: (error: unknown) => message.error(error instanceof Error ? error.message : '取消失败'),
  });

  const columns = [
    {
      title: '流程', dataIndex: 'name',
      render: (name: string, record: WorkflowRun) => (
        <div><Typography.Text strong>{name}</Typography.Text><div><Typography.Text type="secondary" style={{ fontSize: 12 }}>#{record.id} · {record.workflow_type === 'excel_delivery' ? 'Excel 交付' : '文件交付'}</Typography.Text></div></div>
      ),
    },
    { title: '项目', dataIndex: 'project_id', width: 170, render: (id: number) => projectMap.get(id)?.name ?? `#${id}` },
    { title: '状态', dataIndex: 'status', width: 120, render: (value: string) => <StatusChip style={WORKFLOW_STATUS[value] ?? WORKFLOW_STATUS.draft} /> },
    { title: '当前阶段', dataIndex: 'current_stage', width: 150, render: (value?: string | null) => value ? <Tag>{value}</Tag> : '—' },
    { title: '进度', dataIndex: 'progress', width: 190, render: (value: number) => <Progress percent={value} size="small" /> },
    { title: '更新时间', dataIndex: 'updated_at', width: 170, render: (value: string) => <Typography.Text type="secondary">{fmtDateTime(value)}</Typography.Text> },
    { title: '操作', width: 90, align: 'right' as const, render: (_: unknown, record: WorkflowRun) => <Button type="text" icon={<EyeOutlined />} onClick={() => setDetailId(record.id)}>详情</Button> },
  ];

  const detail = detailQ.data as WorkflowDetail | undefined;
  const actionableStage = detail?.stages.find((stage) => ['ready', 'waiting_input', 'waiting_review'].includes(stage.status));

  return (
    <>
      <PageHeader
        title="生产流程"
        subtitle="编排 Excel 与文件交付过程；CAD 图纸业务算法和 Agent 不在本模块范围内"
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
      <Table
        className="surface-table"
        rowKey="id"
        dataSource={workflows}
        columns={columns}
        loading={workflowsQ.isLoading}
        scroll={{ x: 1000 }}
        pagination={{ current: page, pageSize, total: workflowsQ.data?.pagination.total ?? 0, showSizeChanger: true, onChange: (nextPage, nextSize) => { setPage(nextPage); setPageSize(nextSize); } }}
        locale={{ emptyText: <Empty description="暂无生产流程" /> }}
      />

      <Drawer title={detail ? `流程 #${detail.id} · ${detail.name}` : '流程详情'} open={detailId !== null} onClose={() => setDetailId(null)} width={680} loading={detailQ.isLoading} extra={detail && <Space>{detail.status === 'draft' && <Button type="primary" loading={startMutation.isPending} onClick={() => startMutation.mutate(detail.id)}>启动</Button>}{actionableStage && detail.status !== 'draft' && <Button type="primary" loading={completeMutation.isPending} onClick={() => completeMutation.mutate({ id: detail.id, stage: actionableStage.stage_code })}>确认当前阶段</Button>}{!TERMINAL.has(detail.status) && <Popconfirm title="确定取消整个流程？" onConfirm={() => cancelMutation.mutate(detail.id)}><Button danger icon={<StopOutlined />}>取消</Button></Popconfirm>}</Space>}>
        {detail && (
          <>
            <Descriptions className="detail-descriptions" bordered column={1} size="small" items={[
              { key: 'project', label: '项目', children: projectMap.get(detail.project_id)?.name ?? `#${detail.project_id}` },
              { key: 'type', label: '流程类型', children: detail.workflow_type === 'excel_delivery' ? 'Excel 交付' : '文件交付' },
              { key: 'status', label: '状态', children: <StatusChip style={WORKFLOW_STATUS[detail.status] ?? WORKFLOW_STATUS.draft} /> },
              { key: 'progress', label: '整体进度', children: <Progress percent={detail.progress} size="small" /> },
              { key: 'created', label: '创建时间', children: fmtDateTime(detail.created_at) },
            ]} />
            <Typography.Title level={5} style={{ marginTop: 26 }}>流程阶段</Typography.Title>
            <Steps direction="vertical" current={Math.max(0, detail.stages.findIndex((stage) => !['succeeded', 'skipped'].includes(stage.status)))} items={detail.stages.map((stage) => ({
              title: stage.name,
              status: STAGE_STATUS[stage.status] ?? 'wait',
              description: <Space orientation="vertical" size={2}><Typography.Text type="secondary">{stage.stage_code} · {WORKFLOW_STATUS[stage.status]?.label ?? stage.status}</Typography.Text>{stage.job_id && <Typography.Link href={`/jobs`}>任务 #{stage.job_id} · 尝试 #{stage.job_attempt}</Typography.Link>}{stage.error_message && <Typography.Text type="danger">{stage.error_message}</Typography.Text>}</Space>,
            }))} />
            <Typography.Title level={5}>流程产物</Typography.Title>
            {detail.artifacts.length ? detail.artifacts.map((artifact) => <Tag key={artifact.id}>{artifact.artifact_type} · v{artifact.version}</Tag>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无产物" />}
          </>
        )}
      </Drawer>

      <Drawer title="新建生产流程" open={createOpen} onClose={() => setCreateOpen(false)} width={460} extra={<Button type="primary" loading={createMutation.isPending} onClick={() => form.submit()}>创建</Button>}>
        <Form form={form} layout="vertical" requiredMark={false} initialValues={{ workflow_type: 'excel_delivery' }} onFinish={(values) => createMutation.mutate(values)}>
          <Form.Item name="project_id" label="所属项目" rules={[{ required: true, message: '请选择项目' }]}><Select showSearch optionFilterProp="label" placeholder="选择项目" options={(projectsQ.data ?? []).map((project) => ({ value: project.id, label: `${project.code} · ${project.name}` }))} /></Form.Item>
          <Form.Item name="name" label="流程名称" rules={[{ required: true, message: '请输入流程名称' }, { max: 128 }]}><Input placeholder="如 2026-07 零件清单交付" /></Form.Item>
          <Form.Item name="workflow_type" label="流程模板" rules={[{ required: true }]}><Select options={[{ value: 'excel_delivery', label: 'Excel 零件清单交付' }, { value: 'file_delivery', label: '通用文件交付' }]} /></Form.Item>
          <Typography.Paragraph type="secondary">本流程只建立通用上传、处理、确认和交付框架，不包含图纸构件提取、分类、拆板、CAD Worker 或 Agent。</Typography.Paragraph>
        </Form>
      </Drawer>
    </>
  );
}
