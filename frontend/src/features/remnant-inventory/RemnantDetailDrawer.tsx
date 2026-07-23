import { Alert, Button, Descriptions, Divider, Drawer, Empty, Popconfirm, Space, Tag, Typography } from 'antd';
import { DownloadOutlined, EyeOutlined } from '@ant-design/icons';
import type { Remnant } from './types';

interface Props {
  remnant?: Remnant;
  open: boolean;
  canDownload: boolean;
  canManage: boolean;
  actionLoading: boolean;
  downloadLoading: boolean;
  downloadError?: string;
  onClose: () => void;
  onPreview: () => void;
  onDownload: () => void;
  onReserve: () => void;
  onRelease: () => void;
  onMarkUsed: () => void;
  onEdit: () => void;
  onArchive: () => void;
}

export function RemnantDetailDrawer(props: Props) {
  const { remnant } = props;
  return (
    <Drawer title="余料详情" width={520} open={props.open} onClose={props.onClose}>
      {!remnant ? <Empty description="未找到余料" /> : (
        <>
          <div className="remnant-detail-hero">
            <div>
              <Typography.Text type="secondary">余料 #{remnant.id}</Typography.Text>
              <Typography.Title level={3}>{remnant.material_code} · {remnant.thickness_mm} mm</Typography.Title>
            </div>
            <StatusTag status={remnant.status} />
          </div>
          <Descriptions column={1} size="small" bordered items={[
            { key: 'project', label: '项目编号', children: remnant.project_no },
            { key: 'source', label: '原始图纸', children: `${remnant.source_name}（${remnant.source_ext.slice(1).toUpperCase()}）` },
            { key: 'parts', label: '零件编号', children: <Space wrap>{remnant.parts.map((part) => <Tag key={part}>{part}</Tag>)}</Space> },
            { key: 'reserved', label: '预占信息', children: remnant.reserved_by ? `${remnant.reserved_by_name ?? `用户 #${remnant.reserved_by}`} · ${remnant.reserved_at ? new Date(remnant.reserved_at).toLocaleString() : ''}` : '—' },
            { key: 'created', label: '入库时间', children: new Date(remnant.created_at).toLocaleString() },
          ]} />
          <Divider />
          {props.downloadError && (
            <Alert type="error" showIcon message={props.downloadError} style={{ marginBottom: 16 }} />
          )}
          <Space wrap>
            <Button icon={<EyeOutlined />} onClick={props.onPreview}>在线预览</Button>
            <Button
              icon={<DownloadOutlined />}
              disabled={!props.canDownload}
              loading={props.downloadLoading}
              onClick={props.onDownload}
            >下载原图 {remnant.source_ext.slice(1).toUpperCase()}</Button>
            {remnant.status === 'available' && (
              <>
                <Popconfirm title="确认预占这张余料？" onConfirm={props.onReserve}>
                  <Button type="primary" loading={props.actionLoading}>预占余料</Button>
                </Popconfirm>
                {props.canManage && <Button onClick={props.onEdit}>编辑信息</Button>}
                {props.canManage && <Popconfirm title="确认归档这张余料？" onConfirm={props.onArchive}><Button danger>归档</Button></Popconfirm>}
              </>
            )}
            {remnant.status === 'reserved' && props.canDownload && (
              <>
                <Button loading={props.actionLoading} onClick={props.onRelease}>取消预占</Button>
                <Popconfirm title="确认这张余料已经投入使用？此操作不可撤销。" onConfirm={props.onMarkUsed}>
                  <Button danger loading={props.actionLoading}>确认使用</Button>
                </Popconfirm>
              </>
            )}
          </Space>
          {!props.canDownload && remnant.status === 'reserved' && (
            <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
              该余料由其他工人预占，仍可在线预览，但不能下载原图或再次预占。
            </Typography.Paragraph>
          )}
        </>
      )}
    </Drawer>
  );
}

export function StatusTag({ status }: { status: Remnant['status'] }) {
  const values = {
    available: ['可用', 'green'],
    reserved: ['已预占', 'gold'],
    used: ['已使用', 'default'],
    archived: ['已归档', 'default'],
  } as const;
  return <Tag color={values[status][1]}>{values[status][0]}</Tag>;
}
