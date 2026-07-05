import { useCallback, useEffect, useMemo } from 'react';
import {
  Button,
  Progress,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
  Card,
} from 'antd';
import {
  DownloadOutlined,
  ReloadOutlined,
  FileOutlined,
  FileTextOutlined,
  CheckCircleFilled,
  SyncOutlined,
  CloseCircleFilled,
  InboxOutlined,
  CloudOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { listFiles, getFileDownloadUrl } from '../../api/files.api';
import { listJobs, retryJob } from '../../api/jobs.api';
import { FileUpload } from '../../components/FileUpload';
import type { StoredFile } from '../../types/file';
import type { Job } from '../../types/job';

// ── helpers ──────────────────────────────────────────────────────────────────

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const STATUS: Record<string, { color: string; bg: string; label: string; icon: React.ReactNode }> = {
  succeeded:  { color: '#52c41a', bg: '#f6ffed', label: '已完成',   icon: <CheckCircleFilled  style={{ color: '#52c41a' }} /> },
  running:    { color: '#1677ff', bg: '#e6f4ff', label: '转换中',   icon: <SyncOutlined        style={{ color: '#1677ff' }} spin /> },
  queued:     { color: '#faad14', bg: '#fffbe6', label: '排队中',   icon: <SyncOutlined        style={{ color: '#faad14' }} /> },
  failed:     { color: '#ff4d4f', bg: '#fff2f0', label: '失败',     icon: <CloseCircleFilled   style={{ color: '#ff4d4f' }} /> },
  cancelled:  { color: '#8c8c8c', bg: '#fafafa', label: '已取消',   icon: <CloseCircleFilled   style={{ color: '#8c8c8c' }} /> },
};

function triggerDownload(url: string, filename: string) {
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => document.body.removeChild(a), 100);
}

// ── page ─────────────────────────────────────────────────────────────────────

export function FilesPage() {
  const filesQ = useQuery({ queryKey: ['files'], queryFn: listFiles });
  const jobsQ = useQuery({ queryKey: ['jobs'], queryFn: listJobs });

  const hasActive = useMemo(
    () => (jobsQ.data ?? []).some((j) => j.status === 'queued' || j.status === 'running'),
    [jobsQ.data],
  );

  useEffect(() => {
    if (!hasActive) return;
    const id = setInterval(() => { filesQ.refetch(); jobsQ.refetch(); }, 2000);
    return () => clearInterval(id);
  }, [hasActive, filesQ, jobsQ]);

  const jobsByFileId = useMemo(() => {
    const map = new Map<number, Job>();
    for (const j of jobsQ.data ?? []) {
      const fid = (j.params_json as Record<string, unknown> | null)?.file_id as number | undefined;
      if (fid) map.set(fid, j);
    }
    return map;
  }, [jobsQ.data]);

  const refresh = useCallback(() => { filesQ.refetch(); jobsQ.refetch(); }, [filesQ, jobsQ]);

  const handleDownload = useCallback(async (file: StoredFile) => {
    try { const { url } = await getFileDownloadUrl(file.id); triggerDownload(url, file.original_name); }
    catch { message.error('获取下载链接失败'); }
  }, []);

  const handleRetry = useCallback(async (jobId: number) => {
    try { await retryJob(jobId); message.success('已重新提交'); refresh(); }
    catch { message.error('重试失败'); }
  }, [refresh]);

  const files = filesQ.data ?? [];
  const succeeded = files.filter((f) => jobsByFileId.get(f.id)?.status === 'succeeded').length;
  const processing = files.filter((f) => { const s = jobsByFileId.get(f.id)?.status; return s === 'running' || s === 'queued'; }).length;
  const dxfCount = files.filter((f) => f.file_ext === '.dxf').length;
  const totalSize = files.reduce((s, f) => s + f.size_bytes, 0);
  const isFirstLoad = filesQ.isLoading && files.length === 0;

  // ── table columns ──────────────────────────────────────────────────────────

  const columns = [
    { title: '#', dataIndex: 'id', width: 48, align: 'center' as const },
    {
      title: '文件名',
      dataIndex: 'original_name',
      render: (name: string, r: StoredFile) => (
        <Space>
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 32, height: 32, borderRadius: 8,
            background: r.file_ext === '.dxf' ? '#e6f4ff' : '#f5f5f5',
          }}>
            {r.file_ext === '.dxf'
              ? <FileTextOutlined style={{ color: '#1677ff', fontSize: 15 }} />
              : <FileOutlined style={{ color: '#8c8c8c', fontSize: 15 }} />
            }
          </span>
          <Tooltip title={name}>
            <Typography.Text style={{ maxWidth: 300 }} ellipsis>{name}</Typography.Text>
          </Tooltip>
          {r.file_ext === '.dxf' && <Tag color="blue" style={{ fontSize: 11, lineHeight: '18px' }}>DXF</Tag>}
        </Space>
      ),
    },
    {
      title: '大小', dataIndex: 'size_bytes', width: 90, align: 'right' as const,
      render: (v: number) => <Typography.Text type="secondary">{fmtSize(v)}</Typography.Text>,
    },
    {
      title: '转换状态', width: 170,
      render: (_: unknown, record: StoredFile) => {
        const job = jobsByFileId.get(record.id);
        if (!job) return <Typography.Text type="secondary">—</Typography.Text>;
        const s = STATUS[job.status] ?? STATUS.cancelled;
        return (
          <Space size={8}>
            <Tag style={{ color: s.color, background: s.bg, border: 'none', borderRadius: 6 }}>
              {s.icon} <span style={{ marginLeft: 4 }}>{s.label}</span>
            </Tag>
            <Progress percent={job.progress} size="small" style={{ width: 56, margin: 0 }}
              strokeColor={s.color}
              status={job.status === 'failed' ? 'exception' : job.status === 'succeeded' ? 'success' : undefined}
            />
          </Space>
        );
      },
    },
    {
      title: '上传时间', dataIndex: 'created_at', width: 100,
      render: (v: string) => <Typography.Text type="secondary" style={{ fontSize: 13 }}>
        {new Date(v).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
      </Typography.Text>,
    },
    {
      title: '', width: 60, align: 'center' as const,
      render: (_: unknown, record: StoredFile) => {
        const job = jobsByFileId.get(record.id);
        const retryable = job?.status === 'failed' || job?.status === 'cancelled';
        return (
          <Space size={2}>
            <Tooltip title="下载文件"><Button type="text" size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(record)} /></Tooltip>
            {retryable && job && <Tooltip title="重新转换"><Button type="text" size="small" danger icon={<ReloadOutlined />} onClick={() => handleRetry(job.id)} /></Tooltip>}
          </Space>
        );
      },
    },
  ];

  return (
    <>
      {/* page header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>文件管理</Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            上传 DWG 图纸，自动转换为 DXF 格式
          </Typography.Text>
        </div>
        {hasActive && (
          <Tag icon={<SyncOutlined spin />} color="processing" style={{ borderRadius: 6 }}>转换进行中…</Tag>
        )}
      </div>

      {/* stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        {[
          { label: '全部文件', value: files.length, icon: <FileOutlined />, color: '#1677ff', bg: '#e6f4ff' },
          { label: '已转换 DXF', value: dxfCount, icon: <CheckCircleFilled />, color: '#52c41a', bg: '#f6ffed' },
          { label: '处理中', value: processing, icon: <SyncOutlined spin={processing > 0} />, color: '#faad14', bg: '#fffbe6' },
          { label: '存储总量', value: fmtSize(totalSize), icon: <CloudOutlined />, color: '#722ed1', bg: '#f9f0ff' },
        ].map((s) => (
          <div key={s.label} style={{
            background: s.bg, borderRadius: 10, padding: '14px 18px',
            display: 'flex', alignItems: 'center', gap: 12,
          }}>
            <span style={{ fontSize: 22, color: s.color, lineHeight: 1 }}>{s.icon}</span>
            <div>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#1f1f1f', lineHeight: 1.2 }}>{s.value}</div>
              <div style={{ fontSize: 13, color: '#8c8c8c', marginTop: 2 }}>{s.label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* upload */}
      <FileUpload onUploaded={refresh} />

      {/* table */}
      <Table
        rowKey="id"
        dataSource={files}
        columns={columns}
        loading={isFirstLoad}
        size="middle"
        pagination={{ pageSize: 15, showSizeChanger: true, showTotal: (t) => `共 ${t} 个文件` }}
        locale={{
          emptyText: (
            <div style={{ padding: '48px 0' }}>
              <InboxOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />
              <p style={{ fontSize: 14, color: '#bfbfbf', marginTop: 12 }}>暂无文件</p>
              <p style={{ fontSize: 12, color: '#d9d9d9' }}>拖拽 DWG 图纸到上方区域开始上传</p>
            </div>
          ),
        }}
        style={{ background: '#fff', borderRadius: 10 }}
      />
    </>
  );
}
