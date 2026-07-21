import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Drawer, Select, Space, Table, Tag, Typography } from 'antd';

import { getFileTransfer, listFileTransfers } from '../../api/dataAdmin';
import type { FileTransfer } from '../../types/dataAdmin';
import { bytes, DIRECTION_LABELS, OPERATION_LABELS, STATUS_LABELS, stateTag } from './presentation';

export function TransfersPanel() {
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
