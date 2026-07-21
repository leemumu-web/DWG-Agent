import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Drawer, Input, Select, Space, Table, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';

import { getDataAdminFile, listDataAdminFiles } from '../../api/dataAdmin';
import { BUCKETS, bytes, stateTag } from './presentation';

export function FilesPanel() {
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
