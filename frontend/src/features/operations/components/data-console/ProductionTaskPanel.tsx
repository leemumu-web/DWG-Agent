import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Popconfirm,
  Progress,
  Select,
  Space,
  Steps,
  Table,
  Tabs,
  Typography,
} from 'antd';
import {
  ApartmentOutlined,
  EyeOutlined,
  ReloadOutlined,
  StopOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';

import {
  apiErrorRecovery,
  operatorErrorMessage,
  parseApiError,
} from '../../../../shared/api';
import {
  fmtDateTime,
  JOB_STATUS,
  ApiErrorAlert,
  StatCard,
  StatGrid,
  statusOf,
  StatusChip,
} from '../../../../shared/components';
import {
  cancelJob,
  getJobDiagnostics,
  JobProgressBar,
  listJobsPage,
  retryJob,
  type Job,
} from '../../../jobs';
import {
  listWorkflows,
  listWorkflowTemplates,
  WORKFLOW_STATUS,
  type WorkflowRun,
} from '../../../workflows';

interface Props {
  canManage: boolean;
}

const TASK_LABELS: Record<string, string> = {
  convert_dwg_to_dxf: '生产图纸转 DXF',
  convert_dxf_to_dwg: 'DXF 转 DWG',
  extract_dxf_to_excel: 'DXF 提取表格',
  classify_steel_dxf: '生产图纸分类',
  split_steel_dxf: '生产图纸整批拆板',
  process_excel_final: '生产 Excel 整理',
  convert_remnant_dwg: '余料图纸转换',
  parse_remnant_drawing: '余料图纸识别',
};

const ACTIVE_JOB = new Set([
  'pending',
  'queued',
  'running',
  'waiting_cad_worker',
  'validating',
]);
const RETRYABLE_JOB = new Set(['failed', 'cancelled']);

function taskLabel(taskType: string) {
  return TASK_LABELS[taskType] ?? '其他处理任务（类型未识别）';
}

function workflowIdFromJob(job: Job) {
  const value = job.params_json?.workflow_id;
  const workflowId = typeof value === 'number' ? value : Number(value);
  return Number.isInteger(workflowId) && workflowId > 0 ? workflowId : undefined;
}

export function ProductionTaskPanel({ canManage }: Props) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [workflowPage, setWorkflowPage] = useState(1);
  const [jobPage, setJobPage] = useState(1);
  const [jobStatus, setJobStatus] = useState<string>();
  const [detailJob, setDetailJob] = useState<Job>();

  const workflowsQ = useQuery({
    queryKey: ['data-console', 'production-workflows', workflowPage],
    queryFn: () => listWorkflows({
      page: workflowPage,
      page_size: 12,
      workflow_type: 'linux_production',
    }),
    refetchInterval: (query) => (
      query.state.data?.data.some((workflow) => workflow.status === 'running')
        ? 4000
        : false
    ),
  });
  const templatesQ = useQuery({
    queryKey: ['workflow-templates'],
    queryFn: listWorkflowTemplates,
    staleTime: 60_000,
  });
  const jobsQ = useQuery({
    queryKey: ['data-console', 'production-jobs', jobPage, jobStatus],
    queryFn: () => listJobsPage({
      page: jobPage,
      page_size: 20,
      status: jobStatus,
      sort_by: 'updated_at',
      sort_dir: 'desc',
    }),
    refetchInterval: (query) => (
      query.state.data?.data.some((job) => ACTIVE_JOB.has(job.status)) ? 3000 : false
    ),
  });
  const diagnosticsQ = useQuery({
    queryKey: ['job-diagnostics', detailJob?.id],
    queryFn: () => getJobDiagnostics(detailJob!.id),
    enabled: Boolean(detailJob),
  });
  const stageNames = useMemo(() => {
    const production = templatesQ.data?.find((item) => item.code === 'linux_production');
    return new Map(production?.stages.map((stage) => [stage.code, stage.name]) ?? []);
  }, [templatesQ.data]);
  const refresh = () => {
    void workflowsQ.refetch();
    void jobsQ.refetch();
  };
  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['data-console', 'production-jobs'] }),
      queryClient.invalidateQueries({ queryKey: ['data-console', 'production-workflows'] }),
      queryClient.invalidateQueries({ queryKey: ['workflows'] }),
    ]);
  };
  const cancelM = useMutation({
    mutationFn: cancelJob,
    onSuccess: async () => {
      await invalidate();
      message.success('任务已取消；已完成的数据不会被删除');
    },
    onError: (error) => message.error(parseApiError(error, '取消任务失败').message),
  });
  const retryM = useMutation({
    mutationFn: retryJob,
    onSuccess: async () => {
      await invalidate();
      message.success('已创建新的处理尝试，请等待状态更新');
    },
    onError: (error) => message.error(parseApiError(error, '重新处理失败').message),
  });

  const summary = workflowsQ.data?.summary;
  const loadErrors = [
    workflowsQ.isError ? parseApiError(workflowsQ.error, '生产项目加载失败').message : undefined,
    jobsQ.isError ? parseApiError(jobsQ.error, '处理任务加载失败').message : undefined,
  ].filter((message): message is string => Boolean(message));
  const workflowColumns = [
    {
      title: '生产项目',
      key: 'project',
      minWidth: 240,
      render: (_: unknown, workflow: WorkflowRun) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text strong>{workflow.project_code ?? `项目 ${workflow.project_id}`}</Typography.Text>
          <Typography.Text type="secondary">{workflow.project_name ?? workflow.name}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '流程状态',
      dataIndex: 'status',
      width: 120,
      render: (status: string) => <StatusChip style={statusOf(WORKFLOW_STATUS, status)} />,
    },
    {
      title: '当前阶段',
      dataIndex: 'current_stage',
      width: 180,
      render: (stage?: string | null) => stage ? (stageNames.get(stage) ?? '等待后续处理') : '尚未开始',
    },
    {
      title: '进度',
      dataIndex: 'progress',
      width: 150,
      render: (progress: number) => <Progress percent={progress} size="small" />,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 180,
      render: fmtDateTime,
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      align: 'right' as const,
      render: (_: unknown, workflow: WorkflowRun) => (
        <Button type="link" onClick={() => navigate(`/workflows/${workflow.id}`)}>
          继续生产
        </Button>
      ),
    },
  ];
  const jobColumns = [
    {
      title: '处理任务',
      key: 'task',
      minWidth: 230,
      render: (_: unknown, job: Job) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text strong>{taskLabel(job.task_type)}</Typography.Text>
          <Typography.Text type="secondary">任务编号 {job.id} · 第 {job.attempt} 次尝试</Typography.Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 115,
      render: (status: string) => <StatusChip style={statusOf(JOB_STATUS, status)} />,
    },
    {
      title: '进度',
      dataIndex: 'progress',
      width: 160,
      render: (_: number, job: Job) => <JobProgressBar job={job} />,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 180,
      render: fmtDateTime,
    },
    {
      title: '操作',
      key: 'actions',
      width: 250,
      align: 'right' as const,
      render: (_: unknown, job: Job) => {
        const workflowId = workflowIdFromJob(job);
        return (
          <Space size={2}>
            {workflowId && (
              <Button type="link" size="small" onClick={() => navigate(`/workflows/${workflowId}`)}>
                进入项目
              </Button>
            )}
            <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => setDetailJob(job)}>
              {job.error_message || job.error_code ? '查看原因' : '查看进度'}
            </Button>
            {canManage && RETRYABLE_JOB.has(job.status) && job.task_type !== 'split_steel_dxf' && (
              <Button type="link" size="small" icon={<SyncOutlined />} loading={retryM.isPending} onClick={() => retryM.mutate(job.id)}>
                重新处理
              </Button>
            )}
            {canManage && ACTIVE_JOB.has(job.status) && (
              <Popconfirm
                title="确定取消这项处理任务？"
                description="仅停止当前尝试，不会删除已经登记的源文件和结果。"
                onConfirm={() => cancelM.mutate(job.id)}
              >
                <Button type="link" danger size="small" icon={<StopOutlined />} loading={cancelM.isPending}>
                  取消任务
                </Button>
              </Popconfirm>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="当前生产任务"
        description="这里仅汇总正在使用的生产项目和处理任务。项目资料、分类、拆板和 Excel 结果请进入对应生产项目处理；本页不直接修改底层数据库。"
        action={<Button icon={<ReloadOutlined />} onClick={refresh} loading={workflowsQ.isFetching || jobsQ.isFetching}>刷新</Button>}
      />
      <StatGrid>
        <StatCard label="生产项目" value={summary?.total ?? '—'} icon={<ApartmentOutlined />} color="#0f5d66" bg="#e9f8f7" />
        <StatCard label="正在执行" value={summary?.running ?? '—'} icon={<ReloadOutlined spin={(summary?.running ?? 0) > 0} />} color="#2563eb" bg="#eff6ff" />
        <StatCard label="等待操作" value={summary?.waiting ?? '—'} icon={<EyeOutlined />} color="#b45309" bg="#fff8e8" />
        <StatCard label="已经完成" value={summary?.completed ?? '—'} icon={<ApartmentOutlined />} color="#047857" bg="#ecfdf5" />
      </StatGrid>
      {loadErrors.length > 0 && (
        <Alert
          type="error"
          showIcon
          message="部分任务数据加载失败"
          description={`${loadErrors.join('；')}。没有执行任何修改，请按请求编号排查或刷新重试。`}
        />
      )}
      <Card className="console-table-card">
        <Tabs
          items={[
            {
              key: 'workflows',
              label: '生产项目',
              children: (
                <Table<WorkflowRun>
                  rowKey="id"
                  loading={workflowsQ.isLoading}
                  dataSource={workflowsQ.data?.data ?? []}
                  columns={workflowColumns}
                  scroll={{ x: 950 }}
                  pagination={{
                    current: workflowPage,
                    pageSize: 12,
                    total: workflowsQ.data?.pagination.total ?? 0,
                    showSizeChanger: false,
                    onChange: setWorkflowPage,
                  }}
                  locale={{ emptyText: <Empty description="暂无生产项目" /> }}
                />
              ),
            },
            {
              key: 'jobs',
              label: '处理任务',
              children: (
                <Space orientation="vertical" size={12} style={{ width: '100%' }}>
                  <Select
                    allowClear
                    placeholder="按任务状态筛选"
                    value={jobStatus}
                    onChange={(value) => {
                      setJobStatus(value);
                      setJobPage(1);
                    }}
                    options={[
                      { value: 'pending', label: '待处理' },
                      { value: 'queued', label: '排队中' },
                      { value: 'running', label: '运行中' },
                      { value: 'waiting_cad_worker', label: '等待 CAD 处理' },
                      { value: 'validating', label: '校验中' },
                      { value: 'need_review', label: '需要复核' },
                      { value: 'succeeded', label: '成功' },
                      { value: 'failed', label: '失败' },
                      { value: 'cancelled', label: '已取消' },
                    ]}
                    style={{ width: 190 }}
                  />
                  <Table<Job>
                    rowKey="id"
                    loading={jobsQ.isLoading}
                    dataSource={jobsQ.data?.data ?? []}
                    columns={jobColumns}
                    scroll={{ x: 1000 }}
                    pagination={{
                      current: jobPage,
                      pageSize: 20,
                      total: jobsQ.data?.pagination.total ?? 0,
                      showSizeChanger: false,
                      onChange: setJobPage,
                    }}
                    locale={{ emptyText: <Empty description="当前没有处理任务" /> }}
                  />
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Drawer title="任务处理说明" open={Boolean(detailJob)} onClose={() => setDetailJob(undefined)} size={520} destroyOnHidden>
        {detailJob && (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            {(detailJob.error_message || detailJob.error_code) && (
              <Alert
                className="operator-error-alert"
                type="error"
                showIcon
                message={operatorErrorMessage(
                  detailJob.error_code,
                  detailJob.error_message,
                  '这项处理没有完成，请进入所属生产项目核对当前阶段。',
                )}
                description={apiErrorRecovery({
                  message: '',
                  code: detailJob.error_code ?? undefined,
                })}
              />
            )}
            <Descriptions column={1} bordered size="small" items={[
              { key: 'task', label: '处理任务', children: taskLabel(detailJob.task_type) },
              { key: 'job', label: '任务编号', children: detailJob.id },
              { key: 'attempt', label: '处理次数', children: `第 ${detailJob.attempt} 次` },
              { key: 'phase', label: '当前阶段', children: diagnosticsQ.data?.current_phase.label ?? '正在读取' },
              { key: 'updated', label: '最后更新', children: fmtDateTime(detailJob.updated_at) },
            ]} />
            {diagnosticsQ.isError && (
              <ApiErrorAlert
                title="任务阶段读取失败"
                error={diagnosticsQ.error}
                fallback="暂时无法读取任务阶段"
                retryLoading={diagnosticsQ.isFetching}
                onRetry={() => diagnosticsQ.refetch()}
              />
            )}
            {diagnosticsQ.data && (
              <>
                <Alert
                  type="info"
                  showIcon
                  message={diagnosticsQ.data.current_phase.label}
                  description={diagnosticsQ.data.current_phase.message}
                />
                <Steps
                  direction="vertical"
                  size="small"
                  current={diagnosticsQ.data.logs.filter((step) => step.status === 'succeeded').length}
                  items={diagnosticsQ.data.logs.map((step) => ({
                    title: step.label,
                    status: step.status === 'succeeded'
                      ? 'finish'
                      : step.status === 'failed'
                        ? 'error'
                        : 'process',
                    description: `${step.message}${step.duration_seconds == null ? '' : ` · ${step.duration_seconds.toFixed(2)} 秒`}`,
                  }))}
                />
                <Typography.Text type="secondary">{diagnosticsQ.data.message}</Typography.Text>
              </>
            )}
          </Space>
        )}
      </Drawer>
    </Space>
  );
}
