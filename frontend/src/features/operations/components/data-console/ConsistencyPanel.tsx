import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Input, Select, Space, Table, Tag, Typography } from 'antd';
import { ScanOutlined } from '@ant-design/icons';

import { describeApiError } from '../../../../shared/api';
import { useAuthStore } from '../../../../shared/auth';
import {
  getStorageScan,
  listStorageScans,
  listStorageScanFindings,
  previewStorageRemediation,
  startStorageScan,
} from '../../api/dataAdmin';
import type { RemediationAction, RemediationPreview, StorageScanFinding } from '../../types/dataAdmin';
import { RemediationDrawer } from '../RemediationDrawer';
import { ACTIVE_SCAN, bytes, FINDING_LABELS, STATUS_LABELS, stateTag } from './presentation';

export function ConsistencyPanel({ latestScanId }: { latestScanId?: number }) {
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
