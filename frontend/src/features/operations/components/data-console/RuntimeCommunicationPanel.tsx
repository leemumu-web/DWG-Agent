import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Col, Row, Space, Statistic, Table, Tag, Typography } from 'antd';
import { ApiOutlined, ReloadOutlined, WarningOutlined } from '@ant-design/icons';

import {
  getControlPlaneOverview,
  getWindowsNodeContract,
  listControlPlaneEvents,
  listPlatformMessages,
  markPlatformMessageRead,
  queueStaleJobReconciliation,
  type PlatformMessage,
} from '../../api/controlPlane';
import { stateTag } from './presentation';

export function RuntimeCommunicationPanel() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const overview = useQuery({ queryKey: ['control-plane', 'overview'], queryFn: getControlPlaneOverview, refetchInterval: () => document.hidden ? false : 15_000 });
  const events = useQuery({ queryKey: ['control-plane', 'events'], queryFn: listControlPlaneEvents, refetchInterval: () => document.hidden ? false : 15_000 });
  const messages = useQuery({ queryKey: ['control-plane', 'messages'], queryFn: listPlatformMessages, refetchInterval: () => document.hidden ? false : 15_000 });
  const contract = useQuery({ queryKey: ['control-plane', 'windows-contract'], queryFn: getWindowsNodeContract });
  const read = useMutation({ mutationFn: markPlatformMessageRead, onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['control-plane'] }) });
  const recover = useMutation({
    mutationFn: queueStaleJobReconciliation,
    onSuccess: (result) => {
      message.success(`已提交维护任务 ${result.task_id.slice(0, 8)}；仅恢复超过阈值的运行任务。`);
      void queryClient.invalidateQueries({ queryKey: ['control-plane'] });
    },
    onError: () => message.error('维护队列暂不可用；未执行恢复，请检查 Worker 后重试。'),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['control-plane'] });
  const data = overview.data;
  return <Space orientation="vertical" size={18} style={{ width: '100%' }}>
    <Alert type="info" showIcon title="当前运行边界：MySQL SQLAlchemy 队列" description="本页展示真实 SQL 队列、Worker 活动记录和持久化事件。RabbitMQ、Celery Beat、事务 Outbox 与 Windows Node Agent 仍为待实现合同，不提供虚假的控制按钮。" />
    <div className="data-console-metrics">
      <Card><Statistic title="已登记 Worker" value={data?.summary.registered_workers ?? 0} prefix={<ApiOutlined />} /></Card>
      <Card><Statistic title="在线 Worker" value={data?.summary.online_workers ?? 0} /></Card>
      <Card className={(data?.summary.stale_workers ?? 0) ? 'metric-risk' : ''}><Statistic title="活动过期" value={data?.summary.stale_workers ?? 0} prefix={<WarningOutlined />} /></Card>
      <Card><Statistic title="未读运维消息" value={data?.summary.unread_messages ?? 0} prefix={<WarningOutlined />} /></Card>
    </div>
    {(overview.isError || events.isError || messages.isError || contract.isError) && <Alert type="error" showIcon title="运行与通信数据刷新失败" description="保留已有数据；请检查后端连接和管理员/审计员权限。" />}
    <Card title="队列状态" extra={<Button icon={<ReloadOutlined />} onClick={refresh} loading={overview.isFetching}>刷新</Button>}>
      <Typography.Paragraph type="secondary">Broker: <Typography.Text code>{data?.broker.kind ?? '加载中'}</Typography.Text>；就绪消息来源：{data?.broker.ready_count_source ?? '—'}。就绪消息不包括已被 Worker 保留或执行中的任务。</Typography.Paragraph>
      <Table rowKey="name" size="small" loading={overview.isLoading} pagination={false} dataSource={data?.queues ?? []} scroll={{ x: 760 }} columns={[
        { title: '队列', dataIndex: 'name' }, { title: '框架状态', dataIndex: 'mode', render: (value: string) => value === 'active' ? <Tag color="success">已使用</Tag> : <Tag>接口预留</Tag> },
        { title: '业务排队', dataIndex: ['business_jobs', 'queued'] }, { title: '执行中', dataIndex: ['business_jobs', 'running'] }, { title: '失败', dataIndex: ['business_jobs', 'failed'] },
        { title: 'Broker 就绪', dataIndex: 'broker_ready_messages', render: (value: number | null) => value ?? '不可用' },
      ]} />
    </Card>
    <Row gutter={[16, 16]}><Col xs={24} xl={14}><Card title="Worker 活动登记" extra={<Button loading={recover.isPending} onClick={() => recover.mutate()}>恢复超时运行任务</Button>}><Typography.Paragraph type="secondary">仅处理已超过后端 stale timeout 且仍处于 running 的任务；不会重试业务失败、删除文件或启动周期调度。</Typography.Paragraph><Table rowKey="id" size="small" loading={overview.isLoading} pagination={false} dataSource={data?.workers ?? []} scroll={{ x: 780 }} columns={[
      { title: 'Worker', dataIndex: 'worker_name', ellipsis: true }, { title: '状态', dataIndex: 'status', render: stateTag }, { title: '队列', dataIndex: 'queues', render: (value: string[]) => value.length ? value.map((item) => <Tag key={item}>{item}</Tag>) : '—' }, { title: '并发', dataIndex: 'concurrency' }, { title: '最近活动', dataIndex: 'last_seen_at', render: (value: string) => new Date(value).toLocaleString() },
    ]} /></Card></Col><Col xs={24} xl={10}><Card title="Windows Node Agent 合同（待实现）" loading={contract.isLoading}><Typography.Paragraph><Tag color="warning">{contract.data?.status ?? 'pending'}</Tag> {contract.data?.transport}</Typography.Paragraph><Typography.Text type="secondary">未来接口：</Typography.Text>{contract.data?.endpoints.map((endpoint) => <div key={endpoint.path}><Typography.Text code>{endpoint.method} {endpoint.path}</Typography.Text> — {endpoint.purpose}</div>)}<Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>尚不可用：{contract.data?.not_available.join('、')}</Typography.Paragraph></Card></Col></Row>
    <Row gutter={[16, 16]}><Col xs={24} xl={12}><Card title="运维消息"><Table<PlatformMessage> rowKey="id" size="small" loading={messages.isLoading} pagination={false} dataSource={messages.data?.data ?? []} columns={[
      { title: '级别', dataIndex: 'severity', width: 80, render: stateTag }, { title: '内容', key: 'title', render: (_: unknown, row: PlatformMessage) => <><div>{row.title}</div>{row.body && <Typography.Text type="secondary">{row.body}</Typography.Text>}</> }, { title: '状态', dataIndex: 'status', width: 90, render: stateTag }, { title: '操作', key: 'action', width: 70, render: (_: unknown, row: PlatformMessage) => row.status === 'unread' ? <Button type="link" loading={read.isPending} onClick={() => read.mutate(row.id)}>已读</Button> : '—' },
    ]} /></Card></Col><Col xs={24} xl={12}><Card title="通信事件（最近 20 条）"><Table rowKey="id" size="small" loading={events.isLoading} pagination={false} dataSource={events.data?.data ?? []} columns={[
      { title: '事件', dataIndex: 'event_type' }, { title: '目标', dataIndex: 'target_id', render: (value?: string) => value ?? '—' }, { title: '时间', dataIndex: 'created_at', render: (value: string) => new Date(value).toLocaleString() },
    ]} /></Card></Col></Row>
  </Space>;
}
