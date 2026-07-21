import { Button, Progress, Space, Tag, Tooltip, Typography } from 'antd';
import {
  CheckCircleFilled,
  CloseCircleFilled,
  DownloadOutlined,
  EyeOutlined,
  FileOutlined,
  FileTextOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons';

import type { StoredFile } from '../../../files';
import type { Job } from '../../../jobs';

const STATUS: Record<string, { color: string; bg: string; label: string; icon: React.ReactNode }> = {
  succeeded: { color: '#52c41a', bg: '#f6ffed', label: '已完成', icon: <CheckCircleFilled style={{ color: '#52c41a' }} /> },
  pending: { color: '#faad14', bg: '#fffbe6', label: '待排队', icon: <SyncOutlined style={{ color: '#faad14' }} /> },
  running: { color: '#1677ff', bg: '#e6f4ff', label: '转换中', icon: <SyncOutlined style={{ color: '#1677ff' }} spin /> },
  queued: { color: '#faad14', bg: '#fffbe6', label: '排队中', icon: <SyncOutlined style={{ color: '#faad14' }} /> },
  validating: { color: '#1677ff', bg: '#e6f4ff', label: '校验中', icon: <SyncOutlined style={{ color: '#1677ff' }} spin /> },
  waiting_cad_worker: { color: '#1677ff', bg: '#e6f4ff', label: '等待 CAD', icon: <SyncOutlined style={{ color: '#1677ff' }} spin /> },
  failed: { color: '#ff4d4f', bg: '#fff2f0', label: '失败', icon: <CloseCircleFilled style={{ color: '#ff4d4f' }} /> },
  cancelled: { color: '#8c8c8c', bg: '#fafafa', label: '已取消', icon: <CloseCircleFilled style={{ color: '#8c8c8c' }} /> },
};

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface ConversionColumnsOptions {
  tagPending: string;
  tagDone: string;
  resultExt: string;
  downloadResultLabel: string;
  statusLoading: boolean;
  statusLoadFailed: boolean;
  jobsByFileId: Map<number, Job>;
  onPreviewSource: (file: StoredFile) => void;
  onDownloadSource: (file: StoredFile) => void;
  onPreviewResult: (job: Job, sourceName: string) => void;
  onDownloadResult: (job: Job, sourceName: string) => void;
  onRetry: (jobId: number) => void;
}

export function buildConversionColumns(options: ConversionColumnsOptions) {
  return [
    {
      title: '文件名',
      dataIndex: 'original_name',
      render: (name: string) => (
        <Space>
          <Tag style={{ margin: 0, borderRadius: 4, fontSize: 11, lineHeight: '18px', padding: '0 6px' }} color="processing">
            {options.tagPending}
          </Tag>
          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, borderRadius: 8, background: '#f5f5f5' }}>
            <FileOutlined style={{ color: '#8c8c8c', fontSize: 15 }} />
          </span>
          <Tooltip title={name}><Typography.Text style={{ maxWidth: 400 }} ellipsis>{name}</Typography.Text></Tooltip>
        </Space>
      ),
    },
    {
      title: '大小',
      dataIndex: 'size_bytes',
      width: 90,
      align: 'right' as const,
      render: (value: number) => <Typography.Text type="secondary">{fmtSize(value)}</Typography.Text>,
    },
    {
      title: '转换状态',
      width: 280,
      render: (_: unknown, record: StoredFile) => {
        if (options.statusLoading) return <Typography.Text type="secondary">正在加载状态</Typography.Text>;
        if (options.statusLoadFailed) return <Typography.Text type="danger">状态加载失败，请刷新重试</Typography.Text>;
        const job = options.jobsByFileId.get(record.id);
        if (!job) return <Typography.Text type="secondary">未提交</Typography.Text>;
        const status = STATUS[job.status] ?? STATUS.cancelled;
        return (
          <Space size={8}>
            <Tooltip title={job.status === 'failed' ? `${job.error_code || '转换失败'}；可使用“重新提交”再次处理` : undefined}>
              <Tag style={{ color: status.color, background: status.bg, border: 'none', borderRadius: 6 }}>
                {status.icon} <span style={{ marginLeft: 4 }}>{status.label}</span>
              </Tag>
            </Tooltip>
            <Progress
              percent={job.progress}
              size="small"
              style={{ width: 120, margin: 0 }}
              strokeColor={status.color}
              status={job.status === 'failed' ? 'exception' : job.status === 'succeeded' ? 'success' : undefined}
            />
          </Space>
        );
      },
    },
    {
      title: '上传时间',
      dataIndex: 'created_at',
      width: 110,
      render: (value: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
        </Typography.Text>
      ),
    },
    {
      title: '操作',
      width: 140,
      align: 'center' as const,
      render: (_: unknown, record: StoredFile) => {
        const job = options.jobsByFileId.get(record.id);
        const succeeded = job?.status === 'succeeded';
        const failed = job?.status === 'failed' || job?.status === 'cancelled';
        return (
          <Space size={2}>
            {record.file_ext === '.dxf' && (
              <Tooltip title="预览 DXF">
                <Button aria-label="预览 DXF" type="text" size="small" icon={<EyeOutlined style={{ color: '#0891b2' }} />} onClick={() => options.onPreviewSource(record)} />
              </Tooltip>
            )}
            <Tooltip title={`下载 ${options.tagPending}`}>
              <Button aria-label={`下载 ${options.tagPending}`} type="text" size="small" icon={<DownloadOutlined />} onClick={() => options.onDownloadSource(record)} />
            </Tooltip>
            {succeeded && job && (
              <>
                {options.resultExt === '.dxf' && (
                  <Tooltip title="预览 DXF">
                    <Button aria-label="预览 DXF" type="text" size="small" icon={<EyeOutlined style={{ color: '#2563eb' }} />} onClick={() => options.onPreviewResult(job, record.original_name)} />
                  </Tooltip>
                )}
                <Tooltip title={options.downloadResultLabel}>
                  <Button aria-label={options.downloadResultLabel} type="text" size="small" icon={<FileTextOutlined style={{ color: '#1677ff' }} />} onClick={() => options.onDownloadResult(job, record.original_name)} />
                </Tooltip>
              </>
            )}
            {failed && job && (
              <Tooltip title="重新提交转换任务">
                <Button aria-label="重新提交" type="text" size="small" danger icon={<ReloadOutlined />} onClick={() => options.onRetry(job.id)} />
              </Tooltip>
            )}
          </Space>
        );
      },
    },
  ];
}
