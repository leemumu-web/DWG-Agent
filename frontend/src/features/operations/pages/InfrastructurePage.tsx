import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Alert, Space, Tabs, Tag, Typography } from 'antd';
import {
  CloudServerOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';

import { useAuthStore } from '../../../shared/auth';
import { parseApiError } from '../../../shared/api';
import { getDataAdminOverview } from '../api/dataAdmin';
import { ObjectsPanel } from '../components/data-console/ObjectsPanel';
import { ProductionTaskPanel } from '../components/data-console/ProductionTaskPanel';
import { STATUS_LABELS } from '../components/data-console/presentation';

function statusLabel(status?: string) {
  return status ? (STATUS_LABELS[status] ?? '状态异常') : '加载中';
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
  const requestedTab = params.get('tab');
  const active = canManage && ['storage', 'minio'].includes(requestedTab ?? '') ? 'storage' : 'tasks';
  const overview = useQuery({ queryKey: ['data-admin', 'overview', 'shell'], queryFn: getDataAdminOverview });
  const overviewError = overview.isError
    ? parseApiError(overview.error, '数据管理概览加载失败').message
    : undefined;
  const capacity = overview.data?.storage?.capacity;
  const items = [
    {
      key: 'tasks',
      label: '生产任务',
      icon: <DatabaseOutlined />,
      children: <ProductionTaskPanel canManage={canManage} />,
    },
    ...(canManage ? [{
      key: 'storage',
      label: '文件存储',
      icon: <CloudServerOutlined />,
      children: <ObjectsPanel canManage={canManage} areas={overview.data?.storage.areas ?? []} />,
    }] : []),
  ];
  return <div className="data-console">
    <section className="data-console-hero">
      <div><span className="console-kicker">生产数据管理</span><Typography.Title level={2}>数据管理台</Typography.Title><Typography.Text>查看当前生产任务，管理已登记的生产文件</Typography.Text></div>
      <Space wrap>
        <Tag color={!overview.data ? 'default' : overview.data.database.status === 'ok' ? 'success' : 'error'}>业务数据库 {statusLabel(overview.data?.database?.status)}</Tag>
        <Tag color={!overview.data ? 'default' : overview.data.storage.status === 'ok' ? 'success' : 'error'}>文件存储 {statusLabel(overview.data?.storage?.status)}</Tag>
        <Tag
          color={capacity?.status === 'critical' ? 'error' : capacity?.status === 'warning' ? 'orange' : capacity?.status === 'ok' ? 'success' : 'default'}
          title={capacity?.status === 'unknown' ? '容量指标不可用，当前不能判断存储是否接近爆满。' : undefined}
        >
          {capacityLabel(capacity?.status, capacity?.used_percent)}
        </Tag>
        <Tag color={canManage ? 'processing' : 'default'}>{canManage ? '完整操作' : '只读检查'}</Tag>
      </Space>
    </section>
    {overviewError && (
      <Alert
        type="error"
        showIcon
        message="数据管理状态加载失败"
        description={`${overviewError}。任务和文件数据没有被修改，请检查服务器连接后刷新。`}
        style={{ marginTop: 16 }}
      />
    )}
    <Tabs className="data-console-tabs" activeKey={active} onChange={(tab) => setParams({ tab })} items={items} destroyOnHidden />
  </div>;
}
