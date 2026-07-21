import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Col, Descriptions, Row, Space, Statistic, Table, Typography } from 'antd';
import {
  CloudServerOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  SwapOutlined,
  WarningOutlined,
} from '@ant-design/icons';

import { getDataAdminOverview } from '../../api/dataAdmin';
import { getInfrastructureOverview } from '../../api/system';
import { bytes, stateTag } from './presentation';

export function OverviewPanel() {
  const query = useQuery({
    queryKey: ['data-admin', 'overview'],
    queryFn: getDataAdminOverview,
    refetchInterval: () => document.hidden ? false : 30_000,
  });
  const data = query.data;
  const infrastructure = useQuery({ queryKey: ['system', 'infrastructure'], queryFn: getInfrastructureOverview, refetchInterval: () => document.hidden ? false : 30_000 });
  const storageMismatches = (infrastructure.data?.storage.buckets ?? []).filter(
    (bucket) => bucket.object_count !== null && bucket.object_count !== bucket.tracked_files,
  );
  const scanRisk = (data?.latest_scan?.missing_object_count ?? 0)
    + (data?.latest_scan?.untracked_object_count ?? 0)
    + (data?.latest_scan?.size_mismatch_count ?? 0);
  return <Space orientation="vertical" size={18} style={{ width: '100%' }}>
    {query.isError && <Alert type="error" showIcon title="控制台数据刷新失败" description="保留上次成功数据；请检查后端与当前账号权限。" />}
    <div className="data-console-metrics">
      <Card><Statistic title="可用登记" value={data?.catalog.available_files ?? 0} prefix={<DatabaseOutlined />} /></Card>
      <Card><Statistic title="今日入库成功" value={data?.transfers_today.inbound_succeeded ?? 0} prefix={<SwapOutlined />} /></Card>
      <Card><Statistic title="今日出库成功" value={data?.transfers_today.outbound_succeeded ?? 0} prefix={<SwapOutlined />} /></Card>
      <Card className={data?.transfers_today.attention_required ? 'metric-risk' : ''}><Statistic title="流水需关注" value={data?.transfers_today.attention_required ?? 0} prefix={<WarningOutlined />} /></Card>
      <Card><Statistic title="软删除保留" value={data?.catalog.deleted_files ?? 0} prefix={<FileSearchOutlined />} /></Card>
      <Card><Statistic title="登记容量" value={bytes(data?.catalog.tracked_bytes)} prefix={<CloudServerOutlined />} /></Card>
      <Card className={scanRisk ? 'metric-risk' : ''}><Statistic title="最近扫描异常" value={scanRisk} prefix={<WarningOutlined />} /></Card>
    </div>
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={14}>
        <Card title="当前数据源" loading={query.isLoading}>
          <Descriptions column={{ xs: 1, sm: 2 }} items={[
            { key: 'env', label: '运行环境', children: data?.environment.app_env ?? '—' },
            { key: 'database', label: 'MySQL 逻辑库', children: data ? `${data.environment.database_engine} / ${data.environment.database}` : '—' },
            { key: 'storage', label: '对象后端', children: data?.environment.storage_backend ?? '—' },
            { key: 'health', label: '总体状态', children: stateTag(data?.status) },
          ]} />
        </Card>
      </Col>
      <Col xs={24} xl={10}>
        <Card title="最近一致性扫描" loading={query.isLoading}>
          {data?.latest_scan ? <Descriptions column={1} size="small" items={[
            { key: 'status', label: '状态', children: stateTag(data.latest_scan.status) },
            { key: 'missing', label: '对象缺失', children: data.latest_scan.missing_object_count },
            { key: 'untracked', label: '未登记对象', children: data.latest_scan.untracked_object_count },
          ]} /> : <Typography.Text type="secondary">尚未执行扫描</Typography.Text>}
        </Card>
      </Col>
    </Row>
    <Card title="MySQL 与对象存储就绪状态" loading={infrastructure.isLoading} extra={<Button icon={<ReloadOutlined />} onClick={() => infrastructure.refetch()} loading={infrastructure.isFetching}>校验</Button>}>
      {infrastructure.isError ? <Alert type="error" showIcon message="无法读取基础设施状态" description="未执行任何对象修改；请检查管理员权限、数据库与对象存储连接。" /> : <Descriptions column={{ xs: 1, lg: 3 }} size="small" items={[
        { key: 'db', label: 'MySQL', children: <>{stateTag(infrastructure.data?.database.status)} {infrastructure.data?.database.engine ?? '—'} / {infrastructure.data?.database.table_count ?? '—'} 表</> },
        { key: 'storage', label: '当前对象后端', children: <>{stateTag(infrastructure.data?.storage.status)} {infrastructure.data?.storage.backend === 'minio' ? 'MinIO（生产对象存储）' : infrastructure.data?.storage.backend === 'local' ? '本地存储（当前开发运行）' : '—'}</> },
        { key: 'rule', label: '恢复规则', children: infrastructure.data?.recovery.consistency_rule ?? '—' },
      ]} />}
      {infrastructure.data && <Table rowKey="name" size="small" pagination={false} dataSource={infrastructure.data.storage.buckets} style={{ marginTop: 16 }} columns={[
        { title: 'Bucket', dataIndex: 'name' }, { title: 'MySQL 可用登记', dataIndex: 'tracked_files' }, { title: '实际对象数', dataIndex: 'object_count', render: (value: number | null) => value ?? '不可用' },
      ]} />}
      {infrastructure.data && (storageMismatches.length ? <Alert style={{ marginTop: 16 }} type="warning" showIcon message={`Bucket 对账发现 ${storageMismatches.length} 个差额`} description="对象数与 MySQL 可用登记不一致；请在“一致性”页创建扫描，先预检后再处置。" /> : <Alert style={{ marginTop: 16 }} type="success" showIcon message="Bucket 对账：当前可见对象数与 MySQL 可用登记一致。" />)}
    </Card>
  </Space>;
}
