import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Empty,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  CalendarOutlined,
  CheckCircleOutlined,
  DownloadOutlined,
  FileZipOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';

import {
  createDailyArchive,
  getDailyArchive,
  listDailyArchives,
  previewDailyArchive,
} from '../../api/data-admin.api';
import { describeApiError } from '../../api/error';
import { downloadFile } from '../../api/files.api';
import { useAuthStore } from '../../stores/auth.store';
import type { DailyArchivePreview, DailyArchiveRun } from '../../types/data-admin';

const BUCKET_OPTIONS = [
  { value: 'dwg-original', label: 'DWG 原图' },
  { value: 'dxf-original', label: 'DXF 原图' },
  { value: 'dxf-derived', label: 'DXF 分流结果' },
  { value: 'dwg-derived', label: 'DWG 派生结果' },
  { value: 'dwg-reports', label: 'Excel / 报告' },
  { value: 'dwg-temp', label: '临时区' },
];
const ACTIVE = new Set(['queued', 'running']);
const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '生成中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

function bytes(value?: number | null) {
  if (!value) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function statusTag(status: string) {
  const color = status === 'succeeded'
    ? 'success'
    : status === 'failed'
      ? 'error'
      : ACTIVE.has(status)
        ? 'processing'
        : 'default';
  return <Tag color={color}>{STATUS_LABELS[status] ?? status}</Tag>;
}

function distribution(counts: Record<string, number>, empty: string) {
  const entries = Object.entries(counts);
  if (!entries.length) return <Typography.Text type="secondary">{empty}</Typography.Text>;
  return <Space size={[6, 8]} wrap>{entries.map(([key, count]) => <Tag key={key}>{key} · {count}</Tag>)}</Space>;
}

function runProgress(run: DailyArchiveRun) {
  if (run.status === 'succeeded') return { percent: 100, status: 'success' as const, text: '归档包和清单均已登记，可安全下载。' };
  if (run.status === 'failed') return { percent: 100, status: 'exception' as const, text: run.error_message || '归档未完成，请重新预检后再提交。' };
  if (run.status === 'running') return { percent: 68, status: 'active' as const, text: 'Worker 正在流式读取源对象并生成归档，请勿重复提交。' };
  return { percent: 18, status: 'active' as const, text: '任务已进入 maintenance 队列，等待 Worker 领取。' };
}

function archiveIdempotencyKey() {
  const unique = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `daily-archive-${unique}`;
}

export function DailyArchivePanel() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const roles = new Set(user?.roles.map((role) => role.code) ?? []);
  const canExecute = roles.has('super_admin') || roles.has('admin');
  const [archiveDate, setArchiveDate] = useState<Dayjs>(dayjs());
  const [scopeBucket, setScopeBucket] = useState<string>();
  const [preview, setPreview] = useState<DailyArchivePreview>();
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize, setHistoryPageSize] = useState(10);
  const [historyStatus, setHistoryStatus] = useState<string>();
  const [onlySelectedDate, setOnlySelectedDate] = useState(false);
  const [activeRunId, setActiveRunId] = useState<number>();
  const [downloadingFileId, setDownloadingFileId] = useState<number>();
  const [clock, setClock] = useState(dayjs());

  const history = useQuery({
    queryKey: ['data-admin', 'daily-archives', historyPage, historyPageSize, historyStatus, onlySelectedDate ? archiveDate.format('YYYY-MM-DD') : null],
    queryFn: () => listDailyArchives({
      page: historyPage,
      page_size: historyPageSize,
      status: historyStatus,
      archive_date: onlySelectedDate ? archiveDate.format('YYYY-MM-DD') : undefined,
    }),
    refetchInterval: (query) => query.state.data?.data.some((run) => ACTIVE.has(run.status)) ? 3_000 : false,
  });
  const activeRun = useQuery({
    queryKey: ['data-admin', 'daily-archive', activeRunId],
    queryFn: () => getDailyArchive(activeRunId!),
    enabled: Boolean(activeRunId),
    refetchInterval: (query) => ACTIVE.has(query.state.data?.status ?? '') ? 2_000 : false,
  });

  useEffect(() => {
    if (!activeRun.data || ACTIVE.has(activeRun.data.status)) return;
    void queryClient.invalidateQueries({ queryKey: ['data-admin', 'daily-archives'] });
    void queryClient.invalidateQueries({ queryKey: ['data-admin', 'files'] });
    void queryClient.invalidateQueries({ queryKey: ['data-admin', 'transfers'] });
    void queryClient.invalidateQueries({ queryKey: ['data-admin', 'overview'] });
  }, [activeRun.data, queryClient]);
  useEffect(() => {
    if (!preview) return undefined;
    setClock(dayjs());
    const timer = window.setInterval(() => setClock(dayjs()), 1_000);
    return () => window.clearInterval(timer);
  }, [preview]);

  const previewMutation = useMutation({
    mutationFn: () => previewDailyArchive({
      archive_date: archiveDate.format('YYYY-MM-DD'),
      scope_bucket: scopeBucket,
    }),
    onSuccess: (result) => {
      setPreview(result);
      if (result.can_archive) message.success(`预检完成：冻结 ${result.file_count} 个文件，未修改任何源对象。`);
      else message.info(result.block_reason || '当前范围没有可归档文件。');
    },
    onError: (error) => message.error(describeApiError(error, '归档预检失败')),
  });
  const createMutation = useMutation({
    mutationFn: (current: DailyArchivePreview) => createDailyArchive({
      preview_token: current.preview_token,
      idempotency_key: archiveIdempotencyKey(),
    }),
    onSuccess: (run) => {
      setActiveRunId(run.id);
      setPreview(undefined);
      void queryClient.invalidateQueries({ queryKey: ['data-admin', 'daily-archives'] });
      message.success(run.reused
        ? `已复用归档任务 #${run.id}，没有重复生成。`
        : `归档任务 #${run.id} 已提交 maintenance 队列。`);
    },
    onError: (error) => message.error(describeApiError(error, '归档提交失败')),
  });
  const downloadMutation = useMutation({
    mutationFn: ({ fileId, name }: { fileId: number; name: string }) => downloadFile(fileId, name),
    onMutate: ({ fileId }) => setDownloadingFileId(fileId),
    onSuccess: () => message.success('下载已开始'),
    onError: (error) => message.error(describeApiError(error, '归档下载失败')),
    onSettled: () => setDownloadingFileId(undefined),
  });

  const previewExpired = Boolean(preview && dayjs(preview.expires_at).isBefore(clock));
  const previewSecondsRemaining = preview
    ? Math.max(0, dayjs(preview.expires_at).diff(clock, 'second'))
    : 0;
  const scopeLabel = scopeBucket
    ? BUCKET_OPTIONS.find((option) => option.value === scopeBucket)?.label ?? scopeBucket
    : '全部已配置 Bucket';
  const currentRun = activeRun.data;
  const currentProgress = currentRun ? runProgress(currentRun) : undefined;
  const columns = useMemo(() => [
    {
      title: '归档日', dataIndex: 'archive_date', width: 120,
      render: (value: string) => <Typography.Text strong>{value}</Typography.Text>,
    },
    {
      title: '范围', dataIndex: 'scope_bucket', width: 150,
      render: (value?: string | null) => value || '全部 Bucket',
    },
    { title: '状态', dataIndex: 'status', width: 105, render: statusTag },
    { title: '文件', dataIndex: 'file_count', width: 82, align: 'right' as const },
    { title: '源大小', dataIndex: 'total_bytes', width: 110, align: 'right' as const, render: bytes },
    {
      title: '清单 SHA-256', dataIndex: 'source_manifest_sha256', width: 190, ellipsis: true,
      render: (value: string) => <Typography.Text code copyable={{ text: value }}>{value.slice(0, 14)}…</Typography.Text>,
    },
    {
      title: '提交时间', dataIndex: 'created_at', width: 170,
      render: (value: string) => new Date(value).toLocaleString(),
    },
    {
      title: '结果', key: 'result', fixed: 'right' as const, width: 190,
      render: (_: unknown, run: DailyArchiveRun) => run.status === 'succeeded' ? <Space size={2}>
        <Button
          type="link"
          size="small"
          icon={<DownloadOutlined />}
          loading={downloadingFileId === run.archive_file_id}
          disabled={!run.archive_file_id}
          onClick={() => run.archive_file_id && downloadMutation.mutate({ fileId: run.archive_file_id, name: `daily-archive-${run.archive_date}.zip` })}
        >ZIP</Button>
        <Button
          type="link"
          size="small"
          icon={<DownloadOutlined />}
          loading={downloadingFileId === run.manifest_file_id}
          disabled={!run.manifest_file_id}
          onClick={() => run.manifest_file_id && downloadMutation.mutate({ fileId: run.manifest_file_id, name: `daily-archive-${run.archive_date}-manifest.json` })}
        >清单</Button>
      </Space> : run.status === 'failed'
        ? <Space size={2}>
            <Typography.Text type="danger" ellipsis={{ tooltip: `${run.error_code ?? 'DAILY_ARCHIVE_FAILED'}：${run.error_message ?? '归档未完成'}` }} style={{ maxWidth: 68 }}>失败详情</Typography.Text>
            <Button type="link" size="small" onClick={() => { setArchiveDate(dayjs(run.archive_date)); setScopeBucket(run.scope_bucket || undefined); setPreview(undefined); window.scrollTo({ top: 0, behavior: 'smooth' }); }}>重新预检</Button>
          </Space>
        : <Button type="link" size="small" onClick={() => { setActiveRunId(run.id); window.scrollTo({ top: 0, behavior: 'smooth' }); }}>查看进度</Button>,
    },
  ], [downloadMutation, downloadingFileId]);

  function changeScope(value?: string) {
    setScopeBucket(value);
    setPreview(undefined);
  }

  function changeDate(value: Dayjs | null) {
    if (!value) return;
    setArchiveDate(value);
    setPreview(undefined);
    if (onlySelectedDate) setHistoryPage(1);
  }

  return <Space orientation="vertical" size={18} style={{ width: '100%' }}>
    <Alert
      type="info"
      showIcon
      title="非破坏式每日整理"
      description="归档只新增 ZIP、JSON 清单和 MySQL 运行记录；不会移动、重命名、软删除源文件，也不替代数据库/MinIO 灾备。先预检冻结范围，再由 maintenance Worker 异步生成。"
    />

    <Row gutter={[16, 16]}>
      <Col xs={24} xl={10}>
        <Card className="daily-archive-command" title={<Space><CalendarOutlined />归档范围</Space>}>
          <Typography.Text type="secondary">业务日期（{preview?.timezone ?? 'Asia/Shanghai'}）</Typography.Text>
          <DatePicker
            aria-label="归档业务日期"
            value={archiveDate}
            onChange={changeDate}
            allowClear={false}
            disabledDate={(current) => current.isAfter(dayjs(), 'day')}
            style={{ width: '100%', margin: '8px 0 16px' }}
          />
          <Typography.Text type="secondary">文件范围</Typography.Text>
          <Select
            aria-label="归档 Bucket 范围"
            value={scopeBucket}
            onChange={changeScope}
            allowClear
            placeholder="全部已配置 Bucket（推荐）"
            options={BUCKET_OPTIONS}
            style={{ width: '100%', margin: '8px 0 16px' }}
          />
          <div className="daily-archive-scope-note">
            <SafetyCertificateOutlined />
            <span>将选择 {archiveDate.format('YYYY-MM-DD')} 的{scopeLabel}；历史 `daily-archives/` 产物自动排除，防止递归打包。</span>
          </div>
          <Button
            type="primary"
            size="large"
            block
            icon={<CheckCircleOutlined />}
            loading={previewMutation.isPending}
            onClick={() => previewMutation.mutate()}
          >预检归档范围</Button>
        </Card>
      </Col>
      <Col xs={24} xl={14}>
        <Card className="daily-archive-preview" title={<Space><FileZipOutlined />预检结果</Space>}>
          {!preview ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择日期和范围后预检；预检不会创建文件或提交任务。" /> : <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            {!preview.can_archive && <Alert type="warning" showIcon title="当前不能归档" description={preview.block_reason} />}
            {previewExpired && <Alert type="warning" showIcon title="预检已过期" description="为防止使用陈旧清单，请重新预检后提交。" />}
            <div className="daily-archive-metrics">
              <Statistic title="冻结文件" value={preview.file_count} suffix="个" />
              <Statistic title="源文件容量" value={bytes(preview.total_bytes)} />
              <Statistic title="排除历史归档" value={preview.excluded_archive_files} suffix="个" />
            </div>
            <Descriptions size="small" column={{ xs: 1, md: 2 }} items={[
              { key: 'buckets', label: 'Bucket 分布', span: 2, children: distribution(preview.bucket_counts, '无') },
              { key: 'formats', label: '格式分布', span: 2, children: distribution(preview.format_counts, '无') },
              { key: 'window', label: '业务日时间窗口', span: 2, children: `${new Date(preview.window_start).toLocaleString()} — ${new Date(preview.window_end).toLocaleString()}（${preview.timezone}）` },
              { key: 'hash', label: '冻结清单', span: 2, children: <Typography.Text code copyable={{ text: preview.source_manifest_sha256 }}>{preview.source_manifest_sha256}</Typography.Text> },
              { key: 'expiry', label: '确认有效期', children: previewExpired ? '已过期' : `${new Date(preview.expires_at).toLocaleTimeString()}（剩余 ${Math.ceil(previewSecondsRemaining / 60)} 分钟）` },
              { key: 'mode', label: '整理方式', children: <Tag color="success">只新增，不改源文件</Tag> },
            ]} />
            {!canExecute && <Alert type="warning" showIcon title="当前账号为只读数据角色" description="可完成预检和查看历史；只有管理员可以提交归档。" />}
            <Popconfirm
              title={`确认归档 ${preview.file_count} 个文件？`}
              description={`将生成 ZIP 和 JSON 清单，共引用 ${bytes(preview.total_bytes)} 源数据；源文件保持原位。`}
              okText="提交归档"
              cancelText="继续检查"
              onConfirm={() => createMutation.mutate(preview)}
            >
              <Button
                type="primary"
                size="large"
                block
                icon={<FileZipOutlined />}
                loading={createMutation.isPending}
                disabled={!canExecute || !preview.can_archive || previewExpired}
              >确认并生成每日归档</Button>
            </Popconfirm>
          </Space>}
        </Card>
      </Col>
    </Row>

    {activeRun.isError && <Alert type="error" showIcon title="归档任务状态刷新失败" description="已保留页面中的最后状态；请检查网络后点击刷新，不要重复提交归档。" />}
    {currentRun && currentProgress && <Card
      className={`daily-archive-active daily-archive-active--${currentRun.status}`}
      title={<Space>{statusTag(currentRun.status)}<span>归档任务 #{currentRun.id}</span></Space>}
      extra={<Button icon={<ReloadOutlined />} loading={activeRun.isFetching} onClick={() => activeRun.refetch()}>刷新状态</Button>}
    >
      <Progress percent={currentProgress.percent} status={currentProgress.status} showInfo={false} />
      <Row gutter={[16, 12]} style={{ marginTop: 14 }}>
        <Col xs={24} md={10}><Typography.Text>{currentProgress.text}</Typography.Text></Col>
        <Col xs={12} md={4}><Statistic title="文件" value={currentRun.file_count} /></Col>
        <Col xs={12} md={4}><Statistic title="源容量" value={bytes(currentRun.total_bytes)} /></Col>
        <Col xs={24} md={6} className="daily-archive-active-actions">
          {currentRun.status === 'succeeded' && <Space wrap>
            <Button disabled={!currentRun.archive_file_id} icon={<DownloadOutlined />} loading={downloadingFileId === currentRun.archive_file_id} onClick={() => currentRun.archive_file_id && downloadMutation.mutate({ fileId: currentRun.archive_file_id, name: `daily-archive-${currentRun.archive_date}.zip` })}>下载 ZIP</Button>
            <Button disabled={!currentRun.manifest_file_id} icon={<DownloadOutlined />} loading={downloadingFileId === currentRun.manifest_file_id} onClick={() => currentRun.manifest_file_id && downloadMutation.mutate({ fileId: currentRun.manifest_file_id, name: `daily-archive-${currentRun.archive_date}-manifest.json` })}>下载清单</Button>
          </Space>}
        </Col>
      </Row>
    </Card>}

    <Card className="console-table-card" title="每日归档历史" extra={<Space wrap>
      <Select
        aria-label="归档状态筛选"
        allowClear
        value={historyStatus}
        onChange={(value) => { setHistoryStatus(value); setHistoryPage(1); }}
        placeholder="全部状态"
        style={{ width: 130 }}
        options={Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label }))}
      />
      <Space size={6}><Switch aria-label="只看所选日期" size="small" checked={onlySelectedDate} onChange={(checked) => { setOnlySelectedDate(checked); setHistoryPage(1); }} /><Typography.Text type="secondary">只看所选日期</Typography.Text></Space>
      <Button icon={<ReloadOutlined />} loading={history.isFetching} onClick={() => history.refetch()}>刷新</Button>
    </Space>}>
      {history.isError && <Alert type="error" showIcon title="归档历史加载失败" description="保留当前页面状态；请检查后端连接和数据管理权限后重试。" style={{ marginBottom: 16 }} />}
      <Table<DailyArchiveRun>
        rowKey="id"
        size="small"
        loading={history.isLoading}
        dataSource={history.data?.data ?? []}
        columns={columns}
        scroll={{ x: 1180 }}
        pagination={{
          current: historyPage,
          pageSize: historyPageSize,
          total: history.data?.pagination.total ?? 0,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 次归档`,
          onChange: (page, size) => { setHistoryPage(page); setHistoryPageSize(size); },
        }}
      />
    </Card>
  </Space>;
}
