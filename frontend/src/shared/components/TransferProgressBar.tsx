import { LoadingOutlined, StopOutlined } from '@ant-design/icons';
import { Button, Progress, Space, Spin, Typography } from 'antd';

import type { TransferProgress } from '../api/transfer';
import { fmtSize } from './ui';

export function TransferProgressBar({
  label,
  progress,
}: {
  label: string;
  progress: TransferProgress;
}) {
  if (progress.preparing) {
    return (
      <Space style={{ width: '100%', minWidth: 220 }}>
        <Spin indicator={<LoadingOutlined spin />} size="small" />
        <Typography.Text strong>{label}</Typography.Text>
        <Typography.Text type="secondary">服务器正在生成，请稍候…</Typography.Text>
      </Space>
    );
  }
  const total = progress.totalBytes;
  const detail = total
    ? `${fmtSize(progress.loadedBytes)} / ${progress.totalIsEstimated ? '约 ' : ''}${fmtSize(total)}`
    : `已传输 ${fmtSize(progress.loadedBytes)}`;
  return (
    <Space orientation="vertical" size={4} style={{ width: '100%', minWidth: 220 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Typography.Text strong>{label}</Typography.Text>
        <Typography.Text type="secondary">{detail}</Typography.Text>
      </Space>
      <Progress
        aria-label={`${label}进度`}
        percent={progress.percent ?? 0}
        status={progress.completed ? 'success' : 'active'}
        size="small"
        format={(percent) => progress.percent === undefined ? '传输中' : `${percent}%`}
      />
    </Space>
  );
}

/** A transfer progress bar with an inline "cancel" control while a download is active. */
export function CancellableDownloadProgress({
  label,
  progress,
  active,
  onCancel,
}: {
  label: string;
  progress: TransferProgress;
  active: boolean;
  onCancel: () => void;
}) {
  return (
    <Space wrap>
      <TransferProgressBar label={label} progress={progress} />
      {active && (
        <Button size="small" icon={<StopOutlined />} onClick={onCancel}>
          取消下载
        </Button>
      )}
    </Space>
  );
}
