import { Button, Card, Popconfirm, Progress, Space, Statistic, Table, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { RemnantImportBatch, RemnantImportItem } from './types';

interface Props {
  batch: RemnantImportBatch;
  loading: boolean;
  onRetry: (item: RemnantImportItem) => void;
  onCancel: () => void;
}

const labels: Record<RemnantImportItem['status'], [string, string]> = {
  uploaded: ['已登记', 'default'], converting: ['转换中', 'processing'], parsing: ['解析中', 'processing'],
  pending_confirmation: ['待确认', 'gold'], confirmed: ['已入库', 'success'], failed: ['失败', 'error'], cancelled: ['已取消', 'default'],
};

export function RemnantBatchProgress({ batch, loading, onRetry, onCancel }: Props) {
  const done = batch.pending_count + batch.confirmed_count + batch.failed_count + batch.cancelled_count;
  return (
    <Card bordered={false} className="remnant-progress-card">
      <div className="remnant-section-heading">
        <div>
          <Typography.Title level={4}>批次 #{batch.id}</Typography.Title>
          <Typography.Text type="secondary">刷新页面后会根据 URL 中的批次编号恢复当前进度。</Typography.Text>
        </div>
        {!['confirmed', 'cancelled'].includes(batch.status) && (
          <Popconfirm title="取消所有尚未确认的图纸？" onConfirm={onCancel}>
            <Button danger>取消批次</Button>
          </Popconfirm>
        )}
      </div>
      <div className="remnant-stat-grid">
        <Statistic title="图纸总数" value={batch.total_count} />
        <Statistic title="转换中" value={batch.converting_count} />
        <Statistic title="解析中" value={batch.parsing_count} />
        <Statistic title="待确认" value={batch.pending_count} />
        <Statistic title="失败" value={batch.failed_count} valueStyle={{ color: batch.failed_count ? '#cf1322' : undefined }} />
      </div>
      <Progress percent={batch.total_count ? Math.round(done / batch.total_count * 100) : 0} status={batch.failed_count ? 'exception' : 'active'} />
      <Table<RemnantImportItem>
        rowKey="id"
        size="small"
        loading={loading}
        pagination={false}
        dataSource={batch.items}
        columns={[
          { title: '原始文件', dataIndex: 'original_name', ellipsis: true },
          { title: '格式', dataIndex: 'source_ext', width: 80, render: (ext) => ext.slice(1).toUpperCase() },
          { title: '状态', dataIndex: 'status', width: 110, render: (status) => <Tag color={labels[status as RemnantImportItem['status']][1]}>{labels[status as RemnantImportItem['status']][0]}</Tag> },
          { title: '次数', dataIndex: 'attempt', width: 70 },
          { title: '说明', key: 'error', render: (_, row) => row.error_message ?? row.error_code ?? '—' },
          {
            title: '操作', key: 'action', width: 90,
            render: (_, row) => row.status === 'failed' ? <Button type="link" icon={<ReloadOutlined />} onClick={() => onRetry(row)}>重试</Button> : <Space />,
          },
        ]}
      />
    </Card>
  );
}

