import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {
  CloudServerOutlined,
  FileOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  ReloadOutlined,
  UploadOutlined,
} from '@ant-design/icons';

import { downloadFile } from '../../../files';
import {
  deleteDataAdminObject,
  getDataAdminFile,
  getStorageObjectTree,
  moveDataAdminObject,
  uploadDataAdminObject,
} from '../../api/dataAdmin';
import type { StorageObject } from '../../types/dataAdmin';
import { BUCKETS, bytes, stateTag } from './presentation';

interface Props {
  canManage: boolean;
}

export function ObjectsPanel({ canManage }: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [bucket, setBucket] = useState('dwg-original');
  const [prefix, setPrefix] = useState('');
  const [detailId, setDetailId] = useState<number>();
  const [moving, setMoving] = useState<StorageObject>();
  const [moveTarget, setMoveTarget] = useState('');
  const query = useQuery({
    queryKey: ['data-admin', 'object-tree', bucket, prefix],
    queryFn: () => getStorageObjectTree({ bucket, prefix }),
  });
  const detail = useQuery({
    queryKey: ['data-admin', 'object-file-detail', detailId],
    queryFn: () => getDataAdminFile(detailId!),
    enabled: Boolean(detailId),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['data-admin', 'object-tree'] });
  const upload = useMutation({
    mutationFn: uploadDataAdminObject,
    onSuccess: async (stored) => {
      const separator = stored.storage_key.lastIndexOf('/');
      setBucket(stored.bucket);
      setPrefix(separator >= 0 ? stored.storage_key.slice(0, separator + 1) : '');
      await refresh();
      message.success('文件已上传并定位到系统登记目录');
    },
    onError: () => message.error('上传失败，请检查文件类型和权限'),
  });
  const remove = useMutation({
    mutationFn: (record: StorageObject) => deleteDataAdminObject(record.bucket, record.storage_key),
    onSuccess: async () => {
      await refresh();
      message.success('文件已软删除，存储对象保留供审计与恢复');
    },
    onError: () => message.error('删除失败，文件可能被生产流程引用'),
  });
  const move = useMutation({
    mutationFn: () => moveDataAdminObject({
      bucket: moving!.bucket,
      storage_key: moving!.storage_key,
      target_bucket: moving!.bucket,
      target_storage_key: moveTarget.trim(),
    }),
    onSuccess: async () => {
      setMoving(undefined);
      await refresh();
      message.success('对象已重命名或移动');
    },
    onError: () => message.error('移动失败，请检查目标路径是否冲突'),
  });
  const crumbs = useMemo(() => {
    const parts = prefix.split('/').filter(Boolean);
    return [
      { title: bucket, onClick: () => setPrefix('') },
      ...parts.map((part, index) => ({
        title: part,
        onClick: () => setPrefix(`${parts.slice(0, index + 1).join('/')}/`),
      })),
    ];
  }, [bucket, prefix]);

  function chooseBucket(nextBucket: string) {
    setBucket(nextBucket);
    setPrefix('');
  }

  return (
    <Row gutter={[16, 16]} className="storage-browser">
      <Col xs={24} md={7} lg={6}>
        <Card className="console-table-card storage-tree-card" title="MinIO 结构">
          <List
            size="small"
            dataSource={BUCKETS}
            renderItem={(name) => (
              <List.Item
                className={name === bucket ? 'storage-tree-item storage-tree-item--active' : 'storage-tree-item'}
                onClick={() => chooseBucket(name)}
              >
                <Space><CloudServerOutlined /> <Typography.Text>{name}</Typography.Text></Space>
              </List.Item>
            )}
          />
          {prefix && (
            <div className="storage-current-path">
              <FolderOpenOutlined />
              <Typography.Text ellipsis title={prefix}>{prefix}</Typography.Text>
            </div>
          )}
        </Card>
      </Col>
      <Col xs={24} md={17} lg={18}>
        <Card
          className="console-table-card"
          title={<Breadcrumb items={crumbs} />}
          extra={<Space>
            {canManage && (
              <Upload
                showUploadList={false}
                customRequest={({ file, onSuccess, onError }) => {
                  upload.mutate(file as File, {
                    onSuccess: () => onSuccess?.({}),
                    onError: (error) => onError?.(error),
                  });
                }}
              >
                <Button icon={<UploadOutlined />} loading={upload.isPending}>上传到登记目录</Button>
              </Upload>
            )}
            <Button icon={<ReloadOutlined />} onClick={() => query.refetch()} loading={query.isFetching}>刷新</Button>
          </Space>}
        >
          {query.isError && (
            <Alert
              type="error"
              showIcon
              title="MinIO 结构加载失败"
              description="请检查对象存储连接、Bucket 配置与当前账号权限。"
              style={{ marginBottom: 16 }}
            />
          )}
          {query.data?.truncated && (
            <Alert type="warning" showIcon title="当前目录对象较多，仅显示前 5000 个结构节点。" style={{ marginBottom: 16 }} />
          )}
          <div className="storage-folder-grid">
            {query.data?.folders.map((folder) => (
              <button key={folder.prefix} type="button" className="storage-folder" onClick={() => setPrefix(folder.prefix)}>
                <FolderOutlined />
                <span>{folder.name}</span>
              </button>
            ))}
          </div>
          {!query.isLoading && !query.data?.folders.length && !query.data?.objects.length && (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前目录为空" />
          )}
          <Table<StorageObject>
            rowKey="storage_key"
            size="small"
            pagination={false}
            loading={query.isLoading}
            dataSource={query.data?.objects ?? []}
            scroll={{ x: 820 }}
            locale={{ emptyText: query.data?.folders.length ? '当前层级只有文件夹' : undefined }}
            columns={[
              {
                title: '文件',
                dataIndex: 'storage_key',
                ellipsis: true,
                render: (value: string) => <Space><FileOutlined /><span>{value.split('/').pop()}</span></Space>,
              },
              { title: '大小', dataIndex: 'size_bytes', width: 110, align: 'right', render: bytes },
              { title: '最后修改', dataIndex: 'last_modified', width: 190, render: (value?: string) => value ? new Date(value).toLocaleString() : '—' },
              { title: '数据库', dataIndex: 'registered', width: 110, render: (value: boolean) => value ? <Tag color="success">已登记</Tag> : <Tag color="warning">未登记</Tag> },
              {
                title: '检查',
                key: 'actions',
                width: canManage ? 260 : 160,
                render: (_value, record) => record.file_id ? (
                  <Space size={2}>
                    <Button type="link" onClick={() => setDetailId(record.file_id!)}>详情</Button>
                    {canManage && (
                      <Button type="link" onClick={() => downloadFile(record.file_id!, record.storage_key.split('/').pop() || 'download')}>下载</Button>
                    )}
                    {canManage && <>
                      <Button type="link" onClick={() => {
                        setMoving(record);
                        setMoveTarget(record.storage_key);
                      }}>重命名</Button>
                      <Popconfirm
                        title="软删除此文件？"
                        description="数据库会标记删除，底层对象保留供审计和恢复。"
                        onConfirm={() => remove.mutate(record)}
                      >
                        <Button type="link" danger loading={remove.isPending}>删除</Button>
                      </Popconfirm>
                    </>}
                  </Space>
                ) : <Tag color="warning">未登记，仅检查</Tag>,
              },
            ]}
          />
        </Card>
      </Col>
      <Drawer title="文件登记与存储信息" open={Boolean(detailId)} onClose={() => setDetailId(undefined)} size={560} destroyOnHidden>
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
      <Modal
        title="重命名或移动对象"
        open={Boolean(moving)}
        okText="确认移动"
        cancelText="取消"
        confirmLoading={move.isPending}
        okButtonProps={{ disabled: !moveTarget.trim() || moveTarget.trim() === moving?.storage_key }}
        onOk={() => move.mutate()}
        onCancel={() => setMoving(undefined)}
      >
        <Typography.Paragraph type="secondary">
          修改 Bucket 内对象 Key。操作会同步文件登记、流转流水和审计日志。
        </Typography.Paragraph>
        <Input value={moveTarget} onChange={(event) => setMoveTarget(event.target.value)} placeholder="例如 managed/renamed.dwg" />
      </Modal>
    </Row>
  );
}
