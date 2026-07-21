import { Breadcrumb, Button, Progress, Space, Typography } from 'antd';
import {
  ArrowLeftOutlined,
  CheckCircleFilled,
  CloudOutlined,
  FileOutlined,
  FolderOutlined,
  PauseCircleOutlined,
  SyncOutlined,
} from '@ant-design/icons';

import { fmtSize } from './conversionColumns';

export interface ConversionOverviewProps {
  title: string;
  tagDone: string;
  selectedBatch: string | null;
  total: number;
  scopeCount: number;
  totalSize: number;
  succeeded: number;
  failed: number;
  processing: number;
  pendingCount: number;
  aggregateProgress: number;
  statusLoading: boolean;
  statusLoadFailed: boolean;
  hasActive: boolean;
  actionLoading: boolean;
  onBack: () => void;
  onPauseAll: () => void;
  onResumeAll: () => void;
  onRefresh: () => void;
}

export function ConversionOverview(props: ConversionOverviewProps) {
  return (
    <>
      <div className="conversion-header">
        <div>
          {props.selectedBatch ? (
            <Space size={4}>
              <Button type="text" icon={<ArrowLeftOutlined />} onClick={props.onBack}>返回</Button>
              <Breadcrumb items={[
                { title: <a onClick={props.onBack}>全部文件</a> },
                { title: <Space><FolderOutlined />{props.selectedBatch}</Space> },
              ]} />
            </Space>
          ) : null}
        </div>
        <Space>
          {props.hasActive && (
            <Button icon={<PauseCircleOutlined />} loading={props.actionLoading} onClick={props.onPauseAll}>
              全部暂停
            </Button>
          )}
          {!props.statusLoading && !props.statusLoadFailed && props.pendingCount > 0 && (
            <Button type="primary" icon={<SyncOutlined />} loading={props.actionLoading} onClick={props.onResumeAll}>
              提交/重试 {props.pendingCount} 个
            </Button>
          )}
        </Space>
      </div>
      {(props.statusLoading || props.statusLoadFailed || props.scopeCount > 0) && (
        <div className="conversion-progress">
          <SyncOutlined spin={props.statusLoading || props.processing > 0} style={{ fontSize: 20, color: props.statusLoadFailed ? '#ff4d4f' : props.processing > 0 ? '#1677ff' : '#52c41a' }} />
          {props.statusLoading ? (
            <Typography.Text type="secondary">正在加载转换状态…</Typography.Text>
          ) : props.statusLoadFailed ? (
            <Space style={{ flex: 1, justifyContent: 'space-between' }}>
              <Typography.Text type="danger">转换状态加载失败，当前统计可能不完整</Typography.Text>
              <Button size="small" onClick={props.onRefresh}>重新加载</Button>
            </Space>
          ) : (
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, gap: 12, flexWrap: 'wrap' }}>
                <Typography.Text strong>{props.selectedBatch ? `文件夹“${props.selectedBatch}”` : '全部文件'}成功进度</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  成功 {props.succeeded} / {props.scopeCount} · 失败 {props.failed} · 处理中 {props.processing} · 待提交/重试 {props.pendingCount} · {props.aggregateProgress}%
                </Typography.Text>
              </div>
              <Progress percent={props.aggregateProgress} strokeColor={{ '0%': '#1677ff', '100%': '#52c41a' }} size={8} showInfo={false} />
            </div>
          )}
        </div>
      )}
      <div className="conversion-stats">
        {[
          { label: `${props.title}总数`, value: props.total, icon: <FileOutlined />, color: '#2563eb', bg: '#eff6ff' },
          { label: `范围内已转换 ${props.tagDone}`, value: props.statusLoading ? '—' : props.succeeded, icon: <CheckCircleFilled />, color: '#059669', bg: '#ecfdf5' },
          { label: '范围内处理中', value: props.statusLoading ? '—' : props.processing, icon: <SyncOutlined spin={props.processing > 0} />, color: '#d97706', bg: '#fffbeb' },
          { label: '范围内存储量', value: props.statusLoading ? '—' : fmtSize(props.totalSize), icon: <CloudOutlined />, color: '#7c3aed', bg: '#f5f3ff' },
        ].map((stat) => (
          <div key={stat.label} className="conversion-stat">
            <span className="conversion-stat-icon" style={{ color: stat.color, background: stat.bg }}>{stat.icon}</span>
            <div style={{ minWidth: 0 }}>
              <div className="conversion-stat-value">{stat.value}</div>
              <div className="conversion-stat-label">{stat.label}</div>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
