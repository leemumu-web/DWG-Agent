import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Input,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import {
  CloudServerOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  ScanOutlined,
  SwapOutlined,
  WarningOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import {
  getDataAdminOverview,
  getDataAdminFile,
  getFileTransfer,
  getStorageScan,
  listDataAdminFiles,
  listFileTransfers,
  listStorageObjects,
  listStorageScans,
  listStorageScanFindings,
  previewStorageRemediation,
  startStorageScan,
} from '../../api/data-admin.api';
import { getControlPlaneOverview, getWindowsNodeContract, listControlPlaneEvents, listPlatformMessages, markPlatformMessageRead, queueStaleJobReconciliation, type PlatformMessage } from '../../api/control-plane.api';
import { getInfrastructureOverview } from '../../api/system.api';
import { describeApiError } from '../../api/error';
import type {
  FileTransfer,
  RemediationAction,
  RemediationPreview,
  StorageScanFinding,
} from '../../types/data-admin';
import { useAuthStore } from '../../stores/auth.store';
import { RemediationDrawer } from './RemediationDrawer';

const BUCKETS = ['dwg-original', 'dwg-derived', 'dwg-reports', 'dwg-temp', 'dxf-original', 'dxf-derived'];
const ACTIVE_SCAN = new Set(['queued', 'running']);
const STATUS_LABELS: Record<string, string> = {
  succeeded: '成功',
  available: '可用',
  ok: '正常',
  open: '待处置',
  resolved: '已处置',
  queued: '排队中',
  running: '运行中',
  in_progress: '进行中',
  prepared: '已准备',
  failed: '失败',
  error: '异常',
  compensation_required: '待补偿',
  cancelled: '已取消',
  deleted: '已删除',
};
const FINDING_LABELS: Record<string, string> = {
  missing_object: '登记对象缺失',
  untracked_object: '未登记对象',
  size_mismatch: '大小不一致',
  retained_deleted: '软删除对象保留',
};
const DIRECTION_LABELS: Record<string, string> = {
  inbound: '入库',
  outbound: '出库',
  internal: '内部',
};
const OPERATION_LABELS: Record<string, string> = {
  upload: '单文件上传',
  upload_zip: 'ZIP 导入',
  generated: '生成文件登记',
  download: '单文件下载',
  download_zip: 'ZIP 出库',
  soft_delete: '软删除',
  restore: '恢复登记',
  register_existing: '补登记对象',
  soft_delete_missing: '软删除缺失登记',
  purge_untracked: '永久清理对象',
};

function bytes(value?: number | null) {
  if (!value) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function stateTag(status?: string | null) {
  const colors: Record<string, string> = {
    succeeded: 'success', available: 'success', ok: 'success',
    queued: 'processing', running: 'processing', in_progress: 'processing', prepared: 'blue',
    failed: 'error', error: 'error', compensation_required: 'volcano',
    cancelled: 'default', deleted: 'default', resolved: 'success', open: 'warning',
  };
  return <Tag color={colors[status ?? ''] ?? 'default'}>{status ? (STATUS_LABELS[status] ?? status) : '—'}</Tag>;
}

function RuntimeCommunicationPanel() {
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

function OverviewPanel() {
  const query = useQuery({
    queryKey: ['data-admin', 'overview'],
    queryFn: getDataAdminOverview,
    refetchInterval: () => document.hidden ? false : 30_000,
  });
  const data = query.data;
  const infrastructure = useQuery({ queryKey: ['system', 'infrastructure'], queryFn: getInfrastructureOverview, refetchInterval: () => document.hidden ? false : 30_000 });
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
    </Card>
  </Space>;
}

function FilesPanel() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [draft, setDraft] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<string>();
  const [bucket, setBucket] = useState<string>();
  const [fileExt, setFileExt] = useState<string>();
  const [detailId, setDetailId] = useState<number>();
  const query = useQuery({
    queryKey: ['data-admin', 'files', page, pageSize, search, status, bucket, fileExt],
    queryFn: () => listDataAdminFiles({ page, page_size: pageSize, search: search || undefined, status, bucket, file_ext: fileExt }),
  });
  const detail = useQuery({
    queryKey: ['data-admin', 'file-detail', detailId],
    queryFn: () => getDataAdminFile(detailId!),
    enabled: Boolean(detailId),
  });
  return <Card className="console-table-card" title="MySQL 文件登记" extra={
    <Space wrap>
      <Input.Search value={draft} onChange={(event) => setDraft(event.target.value)} onSearch={(value) => { setSearch(value); setPage(1); }} allowClear placeholder="文件名、ID 或 SHA-256" style={{ width: 260 }} />
      <Select allowClear value={status} onChange={(value) => { setStatus(value); setPage(1); }} placeholder="登记状态" style={{ width: 130 }} options={[{ value: 'available', label: '可用' }, { value: 'deleted', label: '软删除' }]} />
      <Select allowClear value={bucket} onChange={(value) => { setBucket(value); setPage(1); }} placeholder="Bucket" style={{ width: 165 }} options={BUCKETS.map((value) => ({ value }))} />
      <Select allowClear value={fileExt} onChange={(value) => { setFileExt(value); setPage(1); }} placeholder="格式" style={{ width: 100 }} options={['.dwg', '.dxf', '.xlsx', '.xls', '.zip'].map((value) => ({ value }))} />
      <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>刷新</Button>
    </Space>
  }>
    {query.isError && <Alert type="error" showIcon title="文件登记加载失败" description="请检查后端连接与当前账号的数据查看权限。" style={{ marginBottom: 16 }} />}
    <Table rowKey="id" size="small" loading={query.isLoading} dataSource={query.data?.data ?? []}
      pagination={{ current: page, pageSize, total: query.data?.pagination.total ?? 0, showSizeChanger: true, onChange: (next, size) => { setPage(next); setPageSize(size); } }}
      scroll={{ x: 1100 }} columns={[
        { title: 'ID', dataIndex: 'id', width: 72 },
        { title: '登记名称', dataIndex: 'original_name', ellipsis: true },
        { title: '格式', dataIndex: 'file_ext', width: 82 },
        { title: '状态', dataIndex: 'status', width: 100, render: stateTag },
        { title: 'Bucket', dataIndex: 'bucket', width: 150 },
        { title: '大小', dataIndex: 'size_bytes', width: 105, align: 'right' as const, render: bytes },
        { title: 'SHA-256', dataIndex: 'sha256', width: 190, ellipsis: true, render: (value: string) => <Typography.Text code copyable={{ text: value }}>{value.slice(0, 16)}…</Typography.Text> },
        { title: '批次', dataIndex: 'batch_name', width: 130, ellipsis: true, render: (value?: string) => value || '—' },
        { title: '操作', key: 'actions', fixed: 'right' as const, width: 80, render: (_value: unknown, record: { id: number }) => <Button type="link" onClick={() => setDetailId(record.id)}>查看</Button> },
      ]} />
    <Drawer title="登记详情" open={Boolean(detailId)} onClose={() => setDetailId(undefined)} size={560} destroyOnHidden>
      {detail.data && <Descriptions column={1} bordered size="small" items={[
        { key: 'id', label: '文件 ID', children: detail.data.id },
        { key: 'name', label: '登记名称', children: detail.data.original_name },
        { key: 'status', label: '状态', children: stateTag(detail.data.status) },
        { key: 'deleted', label: '软删除时间', children: detail.data.deleted_at ? new Date(detail.data.deleted_at).toLocaleString() : '—' },
        { key: 'location', label: '对象位置', children: <Typography.Text code copyable>{`${detail.data.bucket}/${detail.data.storage_key}`}</Typography.Text> },
        { key: 'size', label: '大小', children: bytes(detail.data.size_bytes) },
        { key: 'sha', label: 'SHA-256', children: <Typography.Text code copyable>{detail.data.sha256}</Typography.Text> },
        { key: 'batch', label: '批次', children: detail.data.batch_name || '—' },
        { key: 'created', label: '登记时间', children: new Date(detail.data.created_at).toLocaleString() },
      ]} />}
    </Drawer>
  </Card>;
}

function ObjectsPanel() {
  const [bucket, setBucket] = useState('dwg-original');
  const [prefixDraft, setPrefixDraft] = useState('');
  const [prefix, setPrefix] = useState('');
  const [cursor, setCursor] = useState<string>();
  const [history, setHistory] = useState<(string | undefined)[]>([]);
  const [detailId, setDetailId] = useState<number>();
  const query = useQuery({
    queryKey: ['data-admin', 'objects', bucket, prefix, cursor],
    queryFn: () => listStorageObjects({ bucket, prefix: prefix || undefined, cursor, page_size: 50 }),
  });
  const detail = useQuery({
    queryKey: ['data-admin', 'object-file-detail', detailId],
    queryFn: () => getDataAdminFile(detailId!),
    enabled: Boolean(detailId),
  });
  return <Card className="console-table-card" title="对象存储清单" extra={<Space wrap>
    <Select value={bucket} onChange={(value) => { setBucket(value); setCursor(undefined); setHistory([]); }} style={{ width: 170 }} options={BUCKETS.map((value) => ({ value }))} />
    <Input.Search value={prefixDraft} onChange={(event) => setPrefixDraft(event.target.value)} onSearch={(value) => { setPrefix(value.trim()); setCursor(undefined); setHistory([]); }} allowClear placeholder="对象前缀" style={{ width: 220 }} />
    <Button icon={<ReloadOutlined />} onClick={() => query.refetch()} loading={query.isFetching}>刷新</Button>
  </Space>}>
    {query.isError && <Alert type="error" showIcon title="对象清单加载失败" description="请检查对象存储连接、Bucket 配置与当前账号权限。" style={{ marginBottom: 16 }} />}
    <Table rowKey="storage_key" size="small" pagination={false} loading={query.isLoading} dataSource={query.data?.data ?? []} scroll={{ x: 900 }} columns={[
      { title: '对象 Key', dataIndex: 'storage_key', ellipsis: true },
      { title: '大小', dataIndex: 'size_bytes', width: 110, align: 'right' as const, render: bytes },
      { title: '最后修改', dataIndex: 'last_modified', width: 190, render: (value?: string) => value ? new Date(value).toLocaleString() : '—' },
      { title: 'MySQL 登记', dataIndex: 'registered', width: 120, render: (value: boolean) => value ? <Tag color="success">已登记</Tag> : <Tag color="warning">未登记</Tag> },
      { title: '文件 ID', dataIndex: 'file_id', width: 90, render: (value?: number) => value ?? '—' },
      { title: '操作', key: 'actions', width: 100, render: (_value: unknown, record: { file_id?: number | null }) => record.file_id ? <Button type="link" onClick={() => setDetailId(record.file_id!)}>登记详情</Button> : '—' },
    ]} />
    <div className="cursor-pager"><Button disabled={!history.length} onClick={() => { const next = [...history]; setCursor(next.pop()); setHistory(next); }}>上一页</Button><Button disabled={!query.data?.cursor.next} onClick={() => { setHistory([...history, cursor]); setCursor(query.data?.cursor.next ?? undefined); }}>下一页</Button></div>
    <Drawer title="对象关联登记" open={Boolean(detailId)} onClose={() => setDetailId(undefined)} size={560} destroyOnHidden>
      {detail.data && <Descriptions column={1} bordered size="small" items={[
        { key: 'id', label: '文件 ID', children: detail.data.id },
        { key: 'name', label: '登记名称', children: detail.data.original_name },
        { key: 'status', label: '登记状态', children: stateTag(detail.data.status) },
        { key: 'location', label: '对象位置', children: <Typography.Text code copyable>{`${detail.data.bucket}/${detail.data.storage_key}`}</Typography.Text> },
        { key: 'size', label: '登记大小', children: bytes(detail.data.size_bytes) },
        { key: 'sha', label: 'SHA-256', children: <Typography.Text code copyable>{detail.data.sha256}</Typography.Text> },
        { key: 'updated', label: '最后更新', children: new Date(detail.data.updated_at).toLocaleString() },
      ]} />}
    </Drawer>
  </Card>;
}

function TransfersPanel() {
  const [page, setPage] = useState(1);
  const [direction, setDirection] = useState<string>();
  const [status, setStatus] = useState<string>();
  const [operation, setOperation] = useState<string>();
  const [detailUid, setDetailUid] = useState<string>();
  const query = useQuery({ queryKey: ['data-admin', 'transfers', page, direction, status, operation], queryFn: () => listFileTransfers({ page, page_size: 20, direction, status, operation }) });
  const detail = useQuery({ queryKey: ['data-admin', 'transfer-detail', detailUid], queryFn: () => getFileTransfer(detailUid!), enabled: Boolean(detailUid) });
  const columns = useMemo(() => [
    { title: '方向', dataIndex: 'direction', width: 92, render: (value: string) => <Tag color={value === 'inbound' ? 'cyan' : value === 'outbound' ? 'geekblue' : 'default'}>{DIRECTION_LABELS[value] ?? value}</Tag> },
    { title: '类型', dataIndex: 'operation', width: 150, render: (value: string) => OPERATION_LABELS[value] ?? value },
    { title: '状态', dataIndex: 'status', width: 150, render: stateTag },
    { title: '文件', dataIndex: 'original_name', ellipsis: true, render: (value?: string) => value || '—' },
    { title: '实际字节', dataIndex: 'transferred_bytes', width: 115, align: 'right' as const, render: bytes },
    { title: 'Request ID', dataIndex: 'request_id', width: 190, ellipsis: true },
    { title: '错误', dataIndex: 'error_code', width: 190, ellipsis: true, render: (value?: string) => value || '—' },
    { title: '操作', key: 'actions', fixed: 'right' as const, width: 80, render: (_value: unknown, record: FileTransfer) => <Button type="link" onClick={() => setDetailUid(record.transfer_uid)}>查看</Button> },
  ], []);
  return <Card className="console-table-card" title="入库 / 出库流水" extra={<Space>
    <Select allowClear placeholder="方向" value={direction} onChange={(value) => { setDirection(value); setPage(1); }} style={{ width: 120 }} options={Object.entries(DIRECTION_LABELS).map(([value, label]) => ({ value, label }))} />
    <Select aria-label="流水状态筛选" allowClear placeholder="状态" value={status} onChange={(value) => { setStatus(value); setPage(1); }} style={{ width: 165 }} options={['prepared', 'in_progress', 'succeeded', 'failed', 'cancelled', 'compensation_required'].map((value) => ({ value, label: STATUS_LABELS[value] ?? value }))} />
    <Select allowClear placeholder="流水类型" value={operation} onChange={(value) => { setOperation(value); setPage(1); }} style={{ width: 170 }} options={Object.entries(OPERATION_LABELS).map(([value, label]) => ({ value, label }))} />
  </Space>}>
    {query.isError && <Alert type="error" showIcon title="流转流水加载失败" description="请检查后端连接与当前账号的数据查看权限。" style={{ marginBottom: 16 }} />}
    <Table<FileTransfer> rowKey="transfer_uid" size="small" loading={query.isLoading} dataSource={query.data?.data ?? []} columns={columns} scroll={{ x: 1050 }} pagination={{ current: page, pageSize: 20, total: query.data?.pagination.total ?? 0, onChange: setPage }} />
    <Drawer title="流水详情" open={Boolean(detailUid)} onClose={() => setDetailUid(undefined)} size={560} destroyOnHidden>
      {detail.data && <Descriptions column={1} bordered size="small" items={[
        { key: 'uid', label: 'Transfer UID', children: <Typography.Text code copyable>{detail.data.transfer_uid}</Typography.Text> },
        { key: 'direction', label: '方向 / 操作', children: `${DIRECTION_LABELS[detail.data.direction] ?? detail.data.direction} / ${OPERATION_LABELS[detail.data.operation] ?? detail.data.operation}` },
        { key: 'status', label: '状态', children: stateTag(detail.data.status) },
        { key: 'file', label: '文件', children: detail.data.original_name || '—' },
        { key: 'location', label: '对象位置', children: detail.data.bucket ? <Typography.Text code copyable>{`${detail.data.bucket}/${detail.data.storage_key}`}</Typography.Text> : '—' },
        { key: 'bytes', label: '预期 / 实际', children: `${bytes(detail.data.expected_bytes)} / ${bytes(detail.data.transferred_bytes)}` },
        { key: 'request', label: 'Request ID', children: <Typography.Text code copyable>{detail.data.request_id}</Typography.Text> },
        { key: 'error', label: '错误', children: detail.data.error_code ? `${detail.data.error_code}: ${detail.data.error_message ?? ''}` : '—' },
      ]} />}
    </Drawer>
  </Card>;
}

function ConsistencyPanel({ latestScanId }: { latestScanId?: number }) {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const user = useAuthStore((state) => state.user);
  const roleCodes = new Set(user?.roles.map((role) => role.code) ?? []);
  const canExecute = roleCodes.has('super_admin') || roleCodes.has('admin');
  const [scanId, setScanId] = useState<number | undefined>(latestScanId);
  const [findingPage, setFindingPage] = useState(1);
  const [findingType, setFindingType] = useState<string>();
  const [resolutionStatus, setResolutionStatus] = useState<string>('open');
  const [selectedKeys, setSelectedKeys] = useState<React.Key[]>([]);
  const [action, setAction] = useState<RemediationAction>('purge_untracked');
  const [originalName, setOriginalName] = useState('');
  const [preview, setPreview] = useState<RemediationPreview>();
  useEffect(() => {
    if (!scanId && latestScanId) setScanId(latestScanId);
  }, [latestScanId, scanId]);
  const history = useQuery({
    queryKey: ['data-admin', 'scans'],
    queryFn: () => listStorageScans({ page: 1, page_size: 30 }),
    refetchInterval: (query) => query.state.data?.data.some((item) => ACTIVE_SCAN.has(item.status)) ? 3000 : false,
  });
  const scan = useQuery({ queryKey: ['data-admin', 'scan', scanId], queryFn: () => getStorageScan(scanId!), enabled: Boolean(scanId), refetchInterval: (query) => ACTIVE_SCAN.has(query.state.data?.status ?? '') ? 3000 : false });
  const findings = useQuery({
    queryKey: ['data-admin', 'findings', scanId, findingPage, findingType, resolutionStatus],
    queryFn: () => listStorageScanFindings(scanId!, {
      page: findingPage,
      page_size: 50,
      finding_type: findingType,
      resolution_status: resolutionStatus,
    }),
    enabled: Boolean(scanId) && scan.data?.status === 'succeeded',
  });
  const start = useMutation({ mutationFn: () => startStorageScan(), onSuccess: (data) => { setScanId(data.id); setFindingPage(1); setSelectedKeys([]); void queryClient.invalidateQueries({ queryKey: ['data-admin', 'overview'] }); void queryClient.invalidateQueries({ queryKey: ['data-admin', 'scans'] }); message.success('一致性扫描已启动'); }, onError: (error) => message.error(describeApiError(error, '扫描启动失败')) });
  const previewMutation = useMutation({
    mutationFn: () => previewStorageRemediation({
      finding_ids: selectedKeys.map(Number),
      action,
      metadata: action === 'register_existing' ? { original_name: originalName.trim() } : undefined,
    }),
    onSuccess: setPreview,
    onError: (error) => message.error(describeApiError(error, '处置预检失败')),
  });
  const columns = [
    { title: '异常类型', dataIndex: 'finding_type', width: 170, render: (value: string) => <Tag color={value === 'retained_deleted' ? 'default' : 'warning'}>{FINDING_LABELS[value] ?? value}</Tag> },
    { title: 'Bucket', dataIndex: 'bucket', width: 150 },
    { title: '对象 Key', dataIndex: 'storage_key', ellipsis: true },
    { title: '文件 ID', dataIndex: 'file_id', width: 90, render: (value?: number) => value ?? '—' },
    { title: 'DB 大小', dataIndex: 'database_size_bytes', width: 110, align: 'right' as const, render: bytes },
    { title: '对象大小', dataIndex: 'object_size_bytes', width: 110, align: 'right' as const, render: bytes },
    { title: '处置', dataIndex: 'resolution_status', width: 100, render: stateTag },
  ];
  const selectedFindings = (findings.data?.data ?? []).filter((item) => selectedKeys.includes(item.id));
  const selectedTypes = new Set(selectedFindings.map((item) => item.finding_type));
  const actionOptions = selectedTypes.size === 1
    ? selectedTypes.has('untracked_object')
      ? [
          { value: 'register_existing', label: '补登记现有对象' },
          { value: 'purge_untracked', label: '永久清理未登记对象' },
        ]
      : selectedTypes.has('retained_deleted')
        ? [{ value: 'restore', label: '恢复软删除登记' }]
        : selectedTypes.has('missing_object')
          ? [{ value: 'soft_delete_missing', label: '软删除缺失登记' }]
          : []
    : [];
  useEffect(() => {
    if (actionOptions.length && !actionOptions.some((item) => item.value === action)) {
      setAction(actionOptions[0].value as RemediationAction);
    }
  }, [action, actionOptions]);
  return <Space orientation="vertical" size={16} style={{ width: '100%' }}>
    <Card title="一致性扫描" extra={<Space wrap>
      <Select
        aria-label="扫描历史"
        placeholder="扫描历史"
        value={scanId}
        onChange={(value) => { setScanId(value); setFindingPage(1); setSelectedKeys([]); }}
        style={{ width: 250 }}
        options={(history.data?.data ?? []).map((item) => ({
          value: item.id,
          label: `#${item.id} · ${item.scope_bucket ?? '全部 bucket'} · ${STATUS_LABELS[item.status] ?? item.status}`,
        }))}
      />
      <Button type="primary" icon={<ScanOutlined />} disabled={!canExecute} loading={start.isPending || ACTIVE_SCAN.has(scan.data?.status ?? '')} onClick={() => start.mutate()}>开始扫描</Button>
    </Space>}>
      {(history.isError || scan.isError) && <Alert type="error" showIcon title="扫描信息加载失败" description="请检查后端连接与数据管理权限后重试。" style={{ marginBottom: 16 }} />}
      {scan.data ? <div className="scan-strip"><span>{stateTag(scan.data.status)}</span><span>登记 {scan.data.scanned_files}</span><span>对象 {scan.data.scanned_objects}</span><span>缺失 {scan.data.missing_object_count}</span><span>未登记 {scan.data.untracked_object_count}</span><span>大小不符 {scan.data.size_mismatch_count}</span><span>软删除保留 {scan.data.retained_deleted_count}</span></div> : <Typography.Text type="secondary">选择“开始扫描”生成 MySQL 与对象存储的时间点快照。</Typography.Text>}
    </Card>
    <Card className="console-table-card" title="异常明细" extra={<Space wrap>
      <Select
        aria-label="异常类型筛选"
        allowClear
        placeholder="全部异常类型"
        value={findingType}
        onChange={(value) => { setFindingType(value); setFindingPage(1); setSelectedKeys([]); }}
        style={{ width: 170 }}
        options={Object.entries(FINDING_LABELS).map(([value, label]) => ({ value, label }))}
      />
      <Select
        aria-label="处置状态筛选"
        allowClear
        placeholder="全部处置状态"
        value={resolutionStatus}
        onChange={(value) => { setResolutionStatus(value); setFindingPage(1); setSelectedKeys([]); }}
        style={{ width: 150 }}
        options={[
          { value: 'open', label: '待处置' },
          { value: 'resolved', label: '已处置' },
        ]}
      />
      <Select<RemediationAction>
        aria-label="处置动作"
        value={actionOptions.length ? action : undefined}
        onChange={setAction}
        disabled={!actionOptions.length}
        placeholder="先选择同类异常"
        style={{ width: 210 }}
        options={actionOptions}
      />
      {action === 'register_existing' && <Input aria-label="登记显示名" value={originalName} onChange={(event) => setOriginalName(event.target.value)} placeholder="登记显示名，例如 recovered.dwg" style={{ width: 250 }} />}
      <Button disabled={!selectedKeys.length || !actionOptions.length || (action === 'register_existing' && !originalName.trim())} loading={previewMutation.isPending} onClick={() => previewMutation.mutate()}>处置预检</Button>
    </Space>}>
      {findings.isError && <Alert type="error" showIcon title="异常明细加载失败" description="请刷新扫描结果；若仍失败，请检查服务日志中的 Request ID。" style={{ marginBottom: 16 }} />}
      <Table<StorageScanFinding>
        rowKey="id"
        size="small"
        loading={findings.isLoading}
        dataSource={findings.data?.data ?? []}
        rowSelection={{ selectedRowKeys: selectedKeys, onChange: setSelectedKeys, getCheckboxProps: (record) => ({ disabled: record.resolution_status !== 'open' }) }}
        columns={columns}
        scroll={{ x: 1000 }}
        pagination={{ current: findingPage, pageSize: 50, total: findings.data?.pagination.total ?? 0, onChange: (page) => { setFindingPage(page); setSelectedKeys([]); } }}
      />
    </Card>
    <RemediationDrawer
      preview={preview}
      open={Boolean(preview)}
      canExecute={canExecute}
      onClose={() => setPreview(undefined)}
      onExecuted={() => {
        setPreview(undefined);
        setSelectedKeys([]);
        void queryClient.invalidateQueries({ queryKey: ['data-admin'] });
      }}
    />
  </Space>;
}

export function InfrastructurePage() {
  const [params, setParams] = useSearchParams();
  const active = params.get('tab') || 'overview';
  const overview = useQuery({ queryKey: ['data-admin', 'overview', 'shell'], queryFn: getDataAdminOverview });
  const items = [
    { key: 'overview', label: '总览', icon: <DatabaseOutlined />, children: <OverviewPanel /> },
    { key: 'files', label: '文件登记', icon: <FileSearchOutlined />, children: <FilesPanel /> },
    { key: 'objects', label: '存储对象', icon: <CloudServerOutlined />, children: <ObjectsPanel /> },
    { key: 'transfers', label: '流转流水', icon: <SwapOutlined />, children: <TransfersPanel /> },
    { key: 'consistency', label: '一致性', icon: <ScanOutlined />, children: <ConsistencyPanel latestScanId={overview.data?.latest_scan?.id} /> },
    { key: 'runtime', label: '运行与通信', icon: <ApiOutlined />, children: <RuntimeCommunicationPanel /> },
  ];
  return <div className="data-console">
    <section className="data-console-hero">
      <div><span className="console-kicker">DATA CONTROL PLANE</span><Typography.Title level={2}>数据控制台</Typography.Title><Typography.Text>MySQL 登记、对象存储、入库出库与一致性处置的统一视图</Typography.Text></div>
      <Space wrap><Tag color={overview.data?.status === 'ok' ? 'success' : 'warning'}>{overview.data?.status ? (STATUS_LABELS[overview.data.status] ?? overview.data.status) : '加载中'}</Tag><Tag>{overview.data?.environment.app_env ?? '—'}</Tag><Tag>{overview.data?.environment.storage_backend ?? '—'}</Tag></Space>
    </section>
    <Tabs className="data-console-tabs" activeKey={active} onChange={(tab) => setParams({ tab })} items={items} destroyOnHidden />
  </div>;
}
