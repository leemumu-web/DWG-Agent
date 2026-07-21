import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Drawer, Input, Select, Space, Table, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';

import { getDataAdminFile, listStorageObjects } from '../../api/dataAdmin';
import { BUCKETS, bytes, stateTag } from './presentation';

export function ObjectsPanel() {
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
