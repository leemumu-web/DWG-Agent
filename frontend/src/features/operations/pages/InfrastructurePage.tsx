import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Space, Tabs, Tag, Typography } from 'antd';
import {
  CloudServerOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';

import { useAuthStore } from '../../../shared/auth';
import { getDataAdminOverview } from '../api/dataAdmin';
import { MySqlWorkspace } from '../components/data-console/MySqlWorkspace';
import { ObjectsPanel } from '../components/data-console/ObjectsPanel';
import { STATUS_LABELS } from '../components/data-console/presentation';

function statusLabel(status?: string) {
  return status ? (STATUS_LABELS[status] ?? status) : '加载中';
}

function capacityLabel(status?: string, usedPercent?: number | null) {
  if (!status) return '容量加载中';
  if (status === 'unknown' || usedPercent === null || usedPercent === undefined) {
    return '容量未知';
  }
  return `容量 ${usedPercent.toFixed(1)}%`;
}

export function InfrastructurePage() {
  const [params, setParams] = useSearchParams();
  const user = useAuthStore((state) => state.user);
  const canManage = user?.roles.some((role) => ['admin', 'super_admin'].includes(role.code)) ?? false;
  const active = params.get('tab') || 'mysql';
  const overview = useQuery({ queryKey: ['data-admin', 'overview', 'shell'], queryFn: getDataAdminOverview });
  const capacity = overview.data?.storage.capacity;
  const storageLabel = overview.data?.environment.storage_backend === 'minio' ? 'MinIO' : '文件存储';
  const items = [
    {
      key: 'mysql',
      label: 'MySQL',
      icon: <DatabaseOutlined />,
      children: <MySqlWorkspace canManage={canManage} />,
    },
    {
      key: 'minio',
      label: 'MinIO',
      icon: <CloudServerOutlined />,
      children: <ObjectsPanel canManage={canManage} />,
    },
  ];
  return <div className="data-console">
    <section className="data-console-hero">
      <div><span className="console-kicker">DATA CONSOLE</span><Typography.Title level={2}>数据控制台</Typography.Title><Typography.Text>检查和管理 MySQL 数据结构与 MinIO 文件结构</Typography.Text></div>
      <Space wrap>
        <Tag color={overview.data?.database.status === 'ok' ? 'success' : 'error'}>MySQL {statusLabel(overview.data?.database.status)}</Tag>
        <Tag color={overview.data?.storage.status === 'ok' ? 'success' : 'error'}>{storageLabel} {statusLabel(overview.data?.storage.status)}</Tag>
        <Tag
          color={capacity?.status === 'critical' ? 'error' : capacity?.status === 'warning' ? 'orange' : capacity?.status === 'ok' ? 'success' : 'default'}
          title={capacity?.status === 'unknown' ? '容量指标不可用，当前不能判断存储是否接近爆满。' : undefined}
        >
          {capacityLabel(capacity?.status, capacity?.used_percent)}
        </Tag>
        <Tag color={canManage ? 'processing' : 'default'}>{canManage ? '完整操作' : '只读检查'}</Tag>
      </Space>
    </section>
    <Tabs className="data-console-tabs" activeKey={active} onChange={(tab) => setParams({ tab })} items={items} destroyOnHidden />
  </div>;
}
