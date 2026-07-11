import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Col, Descriptions, Progress, Row, Space, Statistic, Table, Tag, Typography } from 'antd';
import { CloudServerOutlined, DatabaseOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { getInfrastructureOverview } from '../../api/system.api';

function bytes(value: number) {
  if (!value) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

export function InfrastructurePage() {
  const query = useQuery({
    queryKey: ['system', 'infrastructure'],
    queryFn: getInfrastructureOverview,
    refetchInterval: 30_000,
  });
  const data = query.data;

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div className="page-header">
        <div>
          <Typography.Title level={2} style={{ margin: 0 }}>数据与存储</Typography.Title>
          <Typography.Text type="secondary">统一查看 MySQL 元数据目录、MinIO 对象桶和恢复边界</Typography.Text>
        </div>
        <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>刷新状态</Button>
      </div>

      {query.isError && <Alert type="error" showIcon message="基础设施状态加载失败" description="请确认当前账号具有管理员权限，并检查后端日志。" />}
      {data?.status === 'degraded' && <Alert type="warning" showIcon message="基础设施处于降级状态" description="MySQL 或对象存储不可达。文件元数据与对象字节必须保持一致，请先恢复服务再执行写入。" />}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={8}>
          <Card loading={query.isLoading} title={<Space><DatabaseOutlined />MySQL</Space>}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Tag color={data?.database.status === 'ok' ? 'success' : 'error'}>{data?.database.status === 'ok' ? '连接正常' : '连接异常'}</Tag>
              <Descriptions size="small" column={1} items={[
                { key: 'engine', label: '引擎', children: data?.database.engine ?? '—' },
                { key: 'database', label: '业务库', children: data?.database.database ?? '—' },
                { key: 'tables', label: '数据表', children: data?.database.table_count ?? '—' },
                { key: 'latency', label: '探测延迟', children: data ? `${data.database.latency_ms} ms` : '—' },
                { key: 'pool', label: '每进程连接池', children: data ? `${data.database.pool.size} + ${data.database.pool.max_overflow}` : '—' },
              ]} />
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card loading={query.isLoading} title={<Space><CloudServerOutlined />对象存储</Space>}>
            <Statistic title="已登记文件" value={data?.catalog.available_files ?? 0} suffix="个" />
            <Statistic title="已登记容量" value={bytes(data?.catalog.tracked_bytes ?? 0)} />
            <Typography.Text type="secondary">后端：{data?.storage.backend ?? '—'} · 探测 {data?.storage.latency_ms ?? '—'} ms</Typography.Text>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card loading={query.isLoading} title={<Space><SafetyCertificateOutlined />恢复一致性</Space>}>
            <Progress percent={data?.recovery.automated_backup ? 100 : 35} status={data?.recovery.automated_backup ? 'success' : 'normal'} showInfo={false} />
            <Typography.Paragraph style={{ marginTop: 12, marginBottom: 8 }}>{data?.recovery.consistency_rule}</Typography.Paragraph>
            <Tag color={data?.recovery.automated_backup ? 'success' : 'warning'}>{data?.recovery.automated_backup ? '已自动备份' : '尚未自动备份'}</Tag>
          </Card>
        </Col>
      </Row>

      <Card title="存储桶与元数据对照" loading={query.isLoading}>
        <Table
          rowKey="name"
          pagination={false}
          dataSource={data?.storage.buckets ?? []}
          columns={[
            { title: '存储桶', dataIndex: 'name' },
            { title: 'MySQL 已登记文件', dataIndex: 'tracked_files', align: 'right' },
            { title: 'MinIO 对象数', dataIndex: 'object_count', align: 'right', render: (value: number | null) => value ?? '—' },
            {
              title: '状态',
              render: (_, row) => row.object_count === null ? <Tag>未统计</Tag> : row.object_count >= row.tracked_files ? <Tag color="success">正常</Tag> : <Tag color="warning">需核查</Tag>,
            },
          ]}
        />
      </Card>
    </Space>
  );
}
