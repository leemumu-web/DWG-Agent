import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Space, Tabs, Tag, Typography } from 'antd';
import {
  ApiOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FileZipOutlined,
  ScanOutlined,
  SwapOutlined,
} from '@ant-design/icons';

import { getDataAdminOverview } from '../api/dataAdmin';
import { ConsistencyPanel } from '../components/data-console/ConsistencyPanel';
import { FilesPanel } from '../components/data-console/FilesPanel';
import { ObjectsPanel } from '../components/data-console/ObjectsPanel';
import { OverviewPanel } from '../components/data-console/OverviewPanel';
import { RuntimeCommunicationPanel } from '../components/data-console/RuntimeCommunicationPanel';
import { STATUS_LABELS } from '../components/data-console/presentation';
import { TransfersPanel } from '../components/data-console/TransfersPanel';
import { DailyArchivePanel } from '../components/DailyArchivePanel';

export function InfrastructurePage() {
  const [params, setParams] = useSearchParams();
  const active = params.get('tab') || 'overview';
  const overview = useQuery({ queryKey: ['data-admin', 'overview', 'shell'], queryFn: getDataAdminOverview });
  const items = [
    { key: 'overview', label: '总览', icon: <DatabaseOutlined />, children: <OverviewPanel /> },
    { key: 'files', label: '文件登记', icon: <FileSearchOutlined />, children: <FilesPanel /> },
    { key: 'objects', label: '存储对象', icon: <CloudServerOutlined />, children: <ObjectsPanel /> },
    { key: 'transfers', label: '流转流水', icon: <SwapOutlined />, children: <TransfersPanel /> },
    { key: 'daily-archive', label: '每日归档', icon: <FileZipOutlined />, children: <DailyArchivePanel /> },
    { key: 'consistency', label: '一致性', icon: <ScanOutlined />, children: <ConsistencyPanel latestScanId={overview.data?.latest_scan?.id} /> },
    { key: 'runtime', label: '运行与通信', icon: <ApiOutlined />, children: <RuntimeCommunicationPanel /> },
  ];
  return <div className="data-console">
    <section className="data-console-hero">
      <div><span className="console-kicker">DATA CONTROL PLANE</span><Typography.Title level={2}>数据控制台</Typography.Title><Typography.Text>MySQL 登记、对象存储、每日归档、入库出库与一致性处置的统一视图</Typography.Text></div>
      <Space wrap><Tag color={overview.data?.status === 'ok' ? 'success' : 'warning'}>{overview.data?.status ? (STATUS_LABELS[overview.data.status] ?? overview.data.status) : '加载中'}</Tag><Tag>{overview.data?.environment.app_env ?? '—'}</Tag><Tag>{overview.data?.environment.storage_backend ?? '—'}</Tag></Space>
    </section>
    <Tabs className="data-console-tabs" activeKey={active} onChange={(tab) => setParams({ tab })} items={items} destroyOnHidden />
  </div>;
}
