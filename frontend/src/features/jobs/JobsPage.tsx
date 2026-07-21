import { useState } from 'react';
import {
  App,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Popconfirm,
  Progress,
  Segmented,
  Space,
  Table,
  Typography,
} from 'antd';
import {
  CloseCircleOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileTextOutlined,
  ReloadOutlined,
  SearchOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  cancelJob,
  createFrameworkSmokeJob,
  getJob,
  getJobResults,
  getJobSteps,
  listJobsPage,
  retryJob,
} from './jobs.api';
import { downloadFile } from '../files';
import { JobTimeline } from './JobTimeline';
import { useJobEvents } from './useJobEvents';
import type { Job, JobStep } from './job';
import {
  fmtDateTime,
  JOB_STATUS,
  PageHeader,
  StatCard,
  StatGrid,
  StatusChip,
  statusOf,
} from '../../shared/components';

const pipelineLabel: Record<string, string> = {
  local_stub: '框架冒烟',
  dxf_open_source: 'DWG → DXF',
  dxf2dwg_open_source: 'DXF → DWG',
  dxf2excel_open_source: 'DXF → Excel',
  excel_final: 'Excel → 零件清单',
  zwcad_worker: '中望 CAD 管线',
};

const taskLabel: Record<string, string> = {
  framework_smoke_test: '框架冒烟',
  convert_dwg_to_dxf: 'DWG → DXF',
  convert_dxf_to_dwg: 'DXF → DWG',
  extract_dxf_to_excel: 'DXF → Excel',
  transform_excel_final: 'Excel → 零件清单',
};

const ACTIVE_STATUSES = new Set(['pending', 'queued', 'running', 'validating', 'waiting_cad_worker']);

export function JobsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [draftSearch, setDraftSearch] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const query = useQuery({
    queryKey: ['jobs', page, pageSize, statusFilter, search],
    queryFn: () => listJobsPage({
      page,
      page_size: pageSize,
      status: statusFilter === 'all' ? undefined : statusFilter,
      search: search || undefined,
    }),
    refetchInterval: 5000,
  });
  const [drawerJobId, setDrawerJobId] = useState<number | null>(null);
  const [drawerJob, setDrawerJob] = useState<Job | null>(null);
  const [drawerSteps, setDrawerSteps] = useState<JobStep[]>([]);
  const [drawerLoading, setDrawerLoading] = useState(false);

  useJobEvents(drawerJobId, (update) => {
    setDrawerJob((prev) => (prev ? { ...prev, ...update.jobPatch } : prev));
    if (update.steps) setDrawerSteps(update.steps);
    queryClient.invalidateQueries({ queryKey: ['jobs'] });
  });

  const jobs = query.data?.data ?? [];

  const activeCount = jobs.filter((job) => ACTIVE_STATUSES.has(job.status)).length;
  const succeededCount = jobs.filter((job) => job.status === 'succeeded').length;
  const failedCount = jobs.filter((job) => job.status === 'failed').length;

  async function openDetail(jobId: number) {
    setDrawerJobId(jobId);
    setDrawerLoading(true);
    try {
      const job = await getJob(jobId);
      const steps = await getJobSteps(jobId, job.attempt);
      setDrawerJob(job);
      setDrawerSteps(steps);
    } catch (error) {
      setDrawerJob(null);
      setDrawerSteps([]);
      message.error(error instanceof Error ? error.message : '任务详情加载失败');
    } finally {
      setDrawerLoading(false);
    }
  }

  async function handleDownloadDxf() {
    if (!drawerJob) return;
    try {
      const results = await getJobResults(drawerJob.id);
      const dxfResult = results.find((result) => result.result_type === 'convert_dwg_to_dxf');
      if (!dxfResult?.result_file_id) {
        message.error('DXF 结果文件未找到');
        return;
      }
      await downloadFile(dxfResult.result_file_id, `job-${drawerJob.id}.dxf`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '下载 DXF 失败');
    }
  }

  const retryMutation = useMutation({
    mutationFn: (jobId: number) => retryJob(jobId),
    onSuccess: async (retried) => {
      message.success('任务已重新提交');
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      if (drawerJobId === retried.id) {
        setDrawerJob(retried);
        setDrawerSteps(await getJobSteps(retried.id, retried.attempt));
      }
    },
    onError: (error: unknown) => message.error(error instanceof Error ? error.message : '重试失败'),
  });

  const cancelMutation = useMutation({
    mutationFn: (jobId: number) => cancelJob(jobId),
    onSuccess: (cancelled) => {
      message.success('任务已取消');
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      if (drawerJobId === cancelled.id) setDrawerJob(cancelled);
    },
    onError: (error: unknown) => message.error(error instanceof Error ? error.message : '取消失败'),
  });

  const smokeMutation = useMutation({
    mutationFn: createFrameworkSmokeJob,
    onSuccess: () => {
      message.success('已创建框架冒烟任务');
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
    onError: (error: unknown) => message.error(error instanceof Error ? error.message : '创建失败'),
  });

  const columns = [
    {
      title: '任务', dataIndex: 'id', width: 220,
      render: (id: number, record: Job) => (
        <div>
          <Typography.Text strong>#{id} · {taskLabel[record.task_type] ?? record.task_type}</Typography.Text>
          <div><Typography.Text type="secondary" style={{ fontSize: 12 }}>{pipelineLabel[record.pipeline ?? ''] ?? record.pipeline ?? '等待分配管线'}</Typography.Text></div>
        </div>
      ),
    },
    { title: '状态', dataIndex: 'status', width: 120, render: (value: string) => <StatusChip style={statusOf(JOB_STATUS, value)} /> },
    {
      title: '进度', dataIndex: 'progress', width: 190,
      render: (value: number, record: Job) => <Progress percent={value} size="small" status={record.status === 'failed' ? 'exception' : record.status === 'succeeded' ? 'success' : undefined} />,
    },
    { title: '尝试', dataIndex: 'attempt', width: 72, align: 'center' as const, render: (value: number) => `#${value}` },
    { title: '创建时间', dataIndex: 'created_at', width: 170, render: (value: string) => <Typography.Text type="secondary">{fmtDateTime(value)}</Typography.Text> },
    {
      title: '操作', width: 150, align: 'right' as const,
      render: (_: unknown, record: Job) => (
        <Space size={2}>
          <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => openDetail(record.id)}>详情</Button>
          {ACTIVE_STATUSES.has(record.status) && (
            <Popconfirm title="确定取消该任务？" okText="取消任务" cancelText="返回" okButtonProps={{ danger: true }} onConfirm={() => cancelMutation.mutate(record.id)}>
              <Button type="text" size="small" danger icon={<StopOutlined />} aria-label={`取消任务 ${record.id}`} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  const isDxfJob = drawerJob?.pipeline === 'dxf_open_source';
  const isSucceeded = drawerJob?.status === 'succeeded';
  const isRetryable = drawerJob?.status === 'failed' || drawerJob?.status === 'cancelled';
  const isCancellable = drawerJob ? ACTIVE_STATUSES.has(drawerJob.status) : false;

  return (
    <>
      <PageHeader
        title="任务中心"
        subtitle="查看转换进度、处理异常并下载任务结果"
        extra={<Space><Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>刷新</Button><Button type="primary" icon={<ThunderboltOutlined />} loading={smokeMutation.isPending} onClick={() => smokeMutation.mutate()}>创建冒烟任务</Button></Space>}
      />

      <StatGrid>
        <StatCard label="任务总数" value={query.data?.pagination.total ?? 0} icon={<ThunderboltOutlined />} color="#2563eb" bg="#eff6ff" />
        <StatCard label="本页处理中" value={activeCount} icon={<ReloadOutlined spin={activeCount > 0} />} color="#d97706" bg="#fffbeb" />
        <StatCard label="本页已完成" value={succeededCount} icon={<FileTextOutlined />} color="#059669" bg="#ecfdf5" />
        <StatCard label="本页失败" value={failedCount} icon={<CloseCircleOutlined />} color="#dc2626" bg="#fef2f2" />
      </StatGrid>

      <div className="table-toolbar">
        <Segmented
          value={statusFilter}
          onChange={(value) => { setStatusFilter(String(value)); setPage(1); }}
          options={[{ label: '全部', value: 'all' }, { label: '处理中', value: 'active' }, { label: '已完成', value: 'succeeded' }, { label: '失败', value: 'failed' }, { label: '已取消', value: 'cancelled' }]}
        />
        <Input.Search allowClear prefix={<SearchOutlined />} placeholder="搜索任务 ID、类型或管线" value={draftSearch} onChange={(event) => setDraftSearch(event.target.value)} onSearch={(value) => { setSearch(value.trim()); setPage(1); }} style={{ width: 280 }} />
      </div>

      <Table
        className="surface-table"
        rowKey="id"
        dataSource={jobs}
        columns={columns}
        loading={query.isLoading}
        scroll={{ x: 920 }}
        pagination={{ current: page, pageSize, total: query.data?.pagination.total ?? 0, pageSizeOptions: [10, 20, 50, 100], showSizeChanger: true, showTotal: (total, range) => `${range[0]}–${range[1]} / 共 ${total} 个任务`, onChange: (nextPage, nextPageSize) => { setPage(nextPageSize === pageSize ? nextPage : 1); setPageSize(nextPageSize); } }}
        locale={{ emptyText: <Empty description={search || statusFilter !== 'all' ? '没有符合条件的任务' : '暂无任务'} /> }}
      />

      <Drawer
        title={<Space><span>任务 #{drawerJobId}</span>{drawerJob && <StatusChip style={statusOf(JOB_STATUS, drawerJob.status)} />}</Space>}
        open={drawerJobId !== null}
        onClose={() => { setDrawerJobId(null); setDrawerJob(null); setDrawerSteps([]); }}
        width={560}
        loading={drawerLoading}
        extra={
          <Space>
            {isCancellable && <Popconfirm title="确定取消该任务？" onConfirm={() => cancelMutation.mutate(drawerJob!.id)}><Button danger icon={<StopOutlined />}>取消</Button></Popconfirm>}
            {isRetryable && <Button icon={<ReloadOutlined />} loading={retryMutation.isPending} onClick={() => retryMutation.mutate(drawerJob!.id)}>重新提交</Button>}
            {isDxfJob && isSucceeded && <Button type="primary" icon={<DownloadOutlined />} onClick={handleDownloadDxf}>下载 DXF</Button>}
          </Space>
        }
      >
        {drawerJob && (
          <>
            <Descriptions className="detail-descriptions" column={1} size="small" bordered>
              <Descriptions.Item label="任务类型">{taskLabel[drawerJob.task_type] ?? drawerJob.task_type}</Descriptions.Item>
              <Descriptions.Item label="处理管线">{pipelineLabel[drawerJob.pipeline ?? ''] ?? drawerJob.pipeline ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="当前进度"><Progress percent={drawerJob.progress} size="small" /></Descriptions.Item>
              <Descriptions.Item label="创建时间">{fmtDateTime(drawerJob.created_at)}</Descriptions.Item>
              <Descriptions.Item label="更新时间">{fmtDateTime(drawerJob.updated_at)}</Descriptions.Item>
              {drawerJob.error_code && <Descriptions.Item label="错误码"><Typography.Text type="danger" code>{drawerJob.error_code}</Typography.Text></Descriptions.Item>}
              {drawerJob.error_message && <Descriptions.Item label="错误信息"><Typography.Text type="danger">{drawerJob.error_message}</Typography.Text></Descriptions.Item>}
            </Descriptions>
            <Typography.Title level={5} style={{ marginTop: 24 }}>处理步骤</Typography.Title>
            {drawerSteps.length > 0 ? <JobTimeline steps={drawerSteps} /> : <Empty description={drawerLoading ? '加载中…' : '暂无步骤'} />}
          </>
        )}
      </Drawer>
    </>
  );
}
