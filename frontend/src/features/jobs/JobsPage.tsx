import { useState } from 'react';
import {
  Button,
  Drawer,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
  message,
  Empty,
} from 'antd';
import {
  DownloadOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { createFrameworkSmokeJob, getJob, getJobSteps, getJobResults, listJobs, retryJob } from '../../api/jobs.api';
import { getResultDownloadUrl } from '../../api/results.api';
import { downloadFile } from '../../api/files.api';
import { JobTimeline } from '../../components/JobTimeline';
import { useJobEvents } from '../../hooks/useJobEvents';
import type { Job, JobStep } from '../../types/job';

const statusColor: Record<string, string> = {
  succeeded: 'success',
  failed: 'error',
  running: 'processing',
  queued: 'warning',
  pending: 'default',
  cancelled: 'default',
};

const pipelineLabel: Record<string, string> = {
  local_stub: '框架冒烟',
  dxf_open_source: 'DXF 开源管线',
  zwcad_worker: '中望 CAD 管线',
};

export function JobsPage() {
  const query = useQuery({ queryKey: ['jobs'], queryFn: listJobs, refetchInterval: 3000 });
  const [drawerJobId, setDrawerJobId] = useState<number | null>(null);
  const [drawerJob, setDrawerJob] = useState<Job | null>(null);
  const [drawerSteps, setDrawerSteps] = useState<JobStep[]>([]);
  const [drawerLoading, setDrawerLoading] = useState(false);

  // SSE: when the drawer is open, subscribe to the live event stream so the
  // progress bar and steps update in real time (no 3s polling lag).
  useJobEvents(drawerJobId, (update) => {
    setDrawerJob((prev) => (prev ? { ...prev, ...update.jobPatch } : prev));
    if (update.steps) setDrawerSteps(update.steps);
  });

  async function openDetail(jobId: number) {
    setDrawerJobId(jobId);
    setDrawerLoading(true);
    try {
      const [job, steps] = await Promise.all([getJob(jobId), getJobSteps(jobId)]);
      setDrawerJob(job);
      setDrawerSteps(steps);
    } catch {
      setDrawerJob(null);
      setDrawerSteps([]);
    }
    setDrawerLoading(false);
  }

  async function handleDownloadDxf() {
    if (!drawerJob) return;
    try {
      const results = await getJobResults(drawerJob.id);
      const dxfResult = results.find((r) => r.result_type === 'convert_dwg_to_dxf');
      if (!dxfResult?.result_file_id) {
        message.error('DXF 结果文件未找到');
        return;
      }
      const sourceName = (drawerJob.params_json as Record<string, unknown> | null)
        ?.file_id ? 'converted' : `job-${drawerJob.id}`;
      await downloadFile(dxfResult.result_file_id, `${sourceName}.dxf`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '下载 DXF 失败');
    }
  }

  async function handleRetry() {
    if (!drawerJob) return;
    try {
      await retryJob(drawerJob.id);
      message.success('已重新提交');
      query.refetch();
      // Refresh drawer
      const [job, steps] = await Promise.all([getJob(drawerJob.id), getJobSteps(drawerJob.id)]);
      setDrawerJob(job);
      setDrawerSteps(steps);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '重试失败');
    }
  }

  async function createSmoke() {
    await createFrameworkSmokeJob();
    message.success('已创建框架冒烟任务');
    query.refetch();
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    {
      title: '任务类型',
      dataIndex: 'task_type',
      width: 160,
      render: (v: string) => {
        const label: Record<string, string> = {
          framework_smoke_test: '框架冒烟',
          convert_dwg_to_dxf: 'DWG→DXF',
        };
        return label[v] ?? v;
      },
    },
    {
      title: '管线',
      dataIndex: 'pipeline',
      width: 120,
      render: (v: string) => pipelineLabel[v] ?? v,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 80,
      render: (v: string) => <Tag color={statusColor[v] || 'default'}>{v}</Tag>,
    },
    {
      title: '进度',
      dataIndex: 'progress',
      width: 150,
      render: (v: number, r: Job) => (
        <Progress
          percent={v}
          size="small"
          status={r.status === 'failed' ? 'exception' : r.status === 'succeeded' ? 'success' : undefined}
        />
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      width: 100,
      render: (_: unknown, record: Job) => (
        <Button size="small" type="link" onClick={() => openDetail(record.id)}>
          查看详情
        </Button>
      ),
    },
  ];

  const isDxfJob = drawerJob?.pipeline === 'dxf_open_source';
  const isSucceeded = drawerJob?.status === 'succeeded';
  const isRetryable = drawerJob?.status === 'failed' || drawerJob?.status === 'cancelled';

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>任务列表</Typography.Title>
        <Button type="primary" onClick={createSmoke}>
          创建框架冒烟任务
        </Button>
      </div>
      <Table
        rowKey="id"
        dataSource={query.data ?? []}
        columns={columns}
        size="middle"
        pagination={{
          defaultPageSize: 20,
          pageSizeOptions: [10, 20, 30, 50, 100],
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (t, range) => `${range[0]}-${range[1]} / 共 ${t} 个任务`,
        }}
      />
      <Drawer
        title={
          <Space>
            <span>任务 #{drawerJobId} 详情</span>
            {drawerJob && (
              <Tag color={statusColor[drawerJob.status] || 'default'}>
                {drawerJob.status}
              </Tag>
            )}
          </Space>
        }
        open={drawerJobId !== null}
        onClose={() => { setDrawerJobId(null); setDrawerJob(null); }}
        width={520}
        loading={drawerLoading}
        extra={
          <Space>
            {isDxfJob && isSucceeded && (
              <Button type="primary" icon={<FileTextOutlined />} onClick={handleDownloadDxf}>
                下载 DXF
              </Button>
            )}
            {isRetryable && (
              <Button icon={<DownloadOutlined />} onClick={handleRetry}>
                重新提交
              </Button>
            )}
          </Space>
        }
      >
        {drawerJob && (
          <div style={{ marginBottom: 24 }}>
            <Typography.Text type="secondary">管线：</Typography.Text>
            <Tag>{pipelineLabel[drawerJob.pipeline ?? ''] ?? drawerJob.pipeline}</Tag>
            <br />
            <Typography.Text type="secondary">类型：</Typography.Text>
            <span>{drawerJob.task_type}</span>
            <br />
            {drawerJob.error_code && (
              <>
                <Typography.Text type="secondary">错误码：</Typography.Text>
                <Typography.Text type="danger">{drawerJob.error_code}</Typography.Text>
                <br />
              </>
            )}
            {drawerJob.error_message && (
              <>
                <Typography.Text type="secondary">错误信息：</Typography.Text>
                <Typography.Text type="danger" style={{ fontSize: 13 }}>
                  {drawerJob.error_message}
                </Typography.Text>
              </>
            )}
          </div>
        )}
        {drawerSteps.length > 0 ? (
          <JobTimeline steps={drawerSteps} />
        ) : (
          <Empty description={drawerLoading ? '加载中…' : '暂无步骤'} />
        )}
      </Drawer>
    </>
  );
}
