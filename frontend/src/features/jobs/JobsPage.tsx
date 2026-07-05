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
import { useQuery } from '@tanstack/react-query';
import { createFrameworkSmokeJob, getJob, getJobSteps, listJobs } from '../../api/jobs.api';
import { JobTimeline } from '../../components/JobTimeline';
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
  const [drawerSteps, setDrawerSteps] = useState<JobStep[]>([]);
  const [drawerLoading, setDrawerLoading] = useState(false);

  async function openDetail(jobId: number) {
    setDrawerJobId(jobId);
    setDrawerLoading(true);
    try {
      const [job, steps] = await Promise.all([getJob(jobId), getJobSteps(jobId)]);
      setDrawerSteps(steps);
    } catch {
      setDrawerSteps([]);
    }
    setDrawerLoading(false);
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

  return (
    <>
      <Typography.Title level={3}>任务列表</Typography.Title>
      <Button type="primary" onClick={createSmoke} style={{ marginBottom: 16 }}>
        创建框架冒烟任务
      </Button>
      <Table
        rowKey="id"
        dataSource={query.data ?? []}
        columns={columns}
        size="middle"
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 个任务` }}
      />
      <Drawer
        title={`任务 #${drawerJobId} 详情`}
        open={drawerJobId !== null}
        onClose={() => setDrawerJobId(null)}
        width={480}
        loading={drawerLoading}
      >
        {drawerSteps.length > 0 ? (
          <JobTimeline steps={drawerSteps} />
        ) : (
          <Empty description={drawerLoading ? '加载中…' : '暂无步骤'} />
        )}
      </Drawer>
    </>
  );
}
