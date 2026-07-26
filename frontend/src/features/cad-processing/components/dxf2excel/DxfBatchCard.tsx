import { Button, Card, Checkbox, Popconfirm, Progress, Space, Tag, Tooltip, Typography } from 'antd';
import {
  CheckCircleFilled,
  CloseCircleFilled,
  CloseOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileExcelOutlined,
  FileTextOutlined,
  FolderOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons';

import type { BatchInfo } from '../../../files';
import type { Job } from '../../../jobs';
import { operatorErrorMessage } from '../../../../shared/api';

const STATUS: Record<string, { color: string; bg: string; label: string; icon: React.ReactNode }> = {
  succeeded: { color: '#52c41a', bg: '#f6ffed', label: '已完成', icon: <CheckCircleFilled style={{ color: '#52c41a' }} /> },
  running: { color: '#1677ff', bg: '#e6f4ff', label: '提取中', icon: <SyncOutlined style={{ color: '#1677ff' }} spin /> },
  queued: { color: '#faad14', bg: '#fffbe6', label: '排队中', icon: <SyncOutlined style={{ color: '#faad14' }} /> },
  failed: { color: '#ff4d4f', bg: '#fff2f0', label: '失败', icon: <CloseCircleFilled style={{ color: '#ff4d4f' }} /> },
  cancelled: { color: '#8c8c8c', bg: '#fafafa', label: '已取消', icon: <CloseCircleFilled style={{ color: '#8c8c8c' }} /> },
};

export interface DxfBatchCardProps {
  batch: BatchInfo;
  job?: Job;
  selected: boolean;
  finalSubmitting: boolean;
  onToggle: (batchName: string) => void;
  onProcess: (batchName: string) => void;
  onPreview: (batchName: string) => void;
  onDownload: (batchName: string) => void;
  onRetry: (batchName: string) => void;
  onCancel: (batchName: string) => void;
  onClear: (batchName: string) => void;
  onDelete: (batchName: string) => void;
  onProcessExcelFinal: (batchName: string) => void;
}

export function DxfBatchCard({
  batch,
  job,
  selected,
  finalSubmitting,
  onToggle,
  onProcess,
  onPreview,
  onDownload,
  onRetry,
  onCancel,
  onClear,
  onDelete,
  onProcessExcelFinal,
}: DxfBatchCardProps) {
  const status = job ? (STATUS[job.status] ?? STATUS.cancelled) : null;
  const isDone = job?.status === 'succeeded';
  const isFailed = job?.status === 'failed' || job?.status === 'cancelled';
  const isProcessing = job?.status === 'running' || job?.status === 'queued';

  return (
    <Card
      hoverable
      size="small"
      style={{
        borderRadius: 10,
        border: selected ? '2px solid #1677ff' : undefined,
        background: selected ? '#e6f4ff' : undefined,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <Checkbox checked={selected} onChange={() => onToggle(batch.name)} style={{ marginTop: 4 }} />
        <FolderOutlined style={{ fontSize: 28, color: '#faad14', marginTop: 2 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <Tooltip title={batch.name}>
            <Typography.Text strong ellipsis style={{ fontSize: 14, maxWidth: 180, display: 'block' }}>
              {batch.name}
            </Typography.Text>
          </Tooltip>
          <div style={{ marginTop: 4 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              <FileTextOutlined style={{ marginRight: 4 }} />
              {batch.file_count} 个 DXF 文件
            </Typography.Text>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            {new Date(batch.latest_created_at).toLocaleString('zh-CN', {
              month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
            })}
          </Typography.Text>
          <div style={{ marginTop: 10 }}>
            {isProcessing && job && status && (
              <div>
                <Tag style={{ color: status.color, background: status.bg, border: 'none', borderRadius: 6, marginBottom: 6 }}>
                  {status.icon} <span style={{ marginLeft: 4 }}>{status.label}</span>
                </Tag>
                <Progress percent={job.progress} size="small" strokeColor={status.color} />
                <div style={{ marginTop: 6 }}>
                  <Button size="small" icon={<PauseCircleOutlined />} onClick={() => onCancel(batch.name)}>暂停</Button>
                </div>
              </div>
            )}
            {isDone && status && (
              <Tag style={{ color: status.color, background: status.bg, border: 'none', borderRadius: 6 }}>
                {status.icon} <span style={{ marginLeft: 4 }}>{status.label}</span>
              </Tag>
            )}
            {isFailed && status && (
              <Tag style={{ color: status.color, background: status.bg, border: 'none', borderRadius: 6 }}>
                {status.icon} <span style={{ marginLeft: 4 }}>{status.label}</span>
                {job && (job.error_code || job.error_message) && (
                  <Tooltip title={operatorErrorMessage(job.error_code, job.error_message, '表格提取未完成，请重新提交。')}>
                    <span style={{ marginLeft: 4, fontSize: 11, color: '#8c8c8c' }}>（查看原因）</span>
                  </Tooltip>
                )}
              </Tag>
            )}
            <div style={{ marginTop: 8 }}>
              {!job && (
                <Button type="primary" size="small" icon={<PlayCircleOutlined />} onClick={() => onProcess(batch.name)}>
                  开始提取
                </Button>
              )}
              {isDone && job && (
                <Space size={4} wrap>
                  <Button type="primary" size="small" icon={<EyeOutlined />} onClick={() => onPreview(batch.name)}>预览</Button>
                  <Tooltip title="下载 Excel">
                    <Button size="small" icon={<DownloadOutlined />} onClick={() => onDownload(batch.name)} />
                  </Tooltip>
                  <Tooltip title="重新提取">
                    <Button size="small" icon={<ReloadOutlined />} onClick={() => onRetry(batch.name)} />
                  </Tooltip>
                  <Popconfirm
                    title="生成最终零件清单？"
                    description="将当前 Excel 结果登记到 Excel Final 管道并开始处理。"
                    okText="确认生成"
                    cancelText="取消"
                    onConfirm={() => onProcessExcelFinal(batch.name)}
                  >
                    <Button size="small" icon={<FileExcelOutlined />} loading={finalSubmitting}>生成零件清单</Button>
                  </Popconfirm>
                  <Popconfirm
                    title={`删除批次 "${batch.name}"？`}
                    description="批次内所有 .dxf 文件将被删除，此操作不可撤销"
                    onConfirm={() => onDelete(batch.name)}
                    okText="确认删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              )}
              {isFailed && job && (
                <Space size={4}>
                  <Button size="small" danger icon={<ReloadOutlined />} onClick={() => onRetry(batch.name)}>重试提取</Button>
                  <Button size="small" icon={<CloseOutlined />} onClick={() => onClear(batch.name)}>取消提取</Button>
                  <Popconfirm
                    title={`删除批次 "${batch.name}"？`}
                    description="批次内所有 .dxf 文件将被删除"
                    onConfirm={() => onDelete(batch.name)}
                    okText="确认删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              )}
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}
