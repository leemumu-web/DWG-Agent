import { useState } from 'react';
import { DeleteOutlined, DownloadOutlined, EyeOutlined, SearchOutlined } from '@ant-design/icons';
import { Alert, App, Button, Card, Form, Input, InputNumber, Popconfirm, Select, Space, Switch, Table, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { operatorErrorMessage } from '../../shared/api';
import { bulkArchiveRemnants, deleteArchivedRemnant, exportAllRemnants, listAllRemnants } from './api';
import { describeRemnantError, describeRemnantErrorAsync } from './errors';
import { StatusTag } from './RemnantDetailDrawer';
import type { BulkArchiveResult, Remnant, RemnantGlobalSearch, RemnantMaterial, RemnantStatus } from './types';

interface Props {
  materials: RemnantMaterial[];
  currentUserId?: number;
  isAdmin: boolean;
  onOpenDetail: (id: number) => void;
}

const activeStatuses: RemnantStatus[] = ['available', 'reserved'];
const allStatuses: RemnantStatus[] = ['available', 'reserved', 'used', 'archived'];
const statusOptions = [
  { label: '可用', value: 'available' },
  { label: '已预留', value: 'reserved' },
  { label: '已领用', value: 'used' },
  { label: '已归档', value: 'archived' },
];
const sortOptions = [
  { label: '最新导入', value: 'created_desc' },
  { label: '最早导入', value: 'created_asc' },
  { label: '厚度从小到大', value: 'thickness_asc' },
  { label: '厚度从大到小', value: 'thickness_desc' },
  { label: '库存状态', value: 'status' },
];
const initialSearch: RemnantGlobalSearch = {
  statuses: activeStatuses,
  sort: 'created_desc',
  page: 1,
};

export function RemnantGlobalPanel({ materials, currentUserId, isAdmin, onOpenDetail }: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [form] = Form.useForm();
  const [search, setSearch] = useState<RemnantGlobalSearch>(initialSearch);
  const [showHistory, setShowHistory] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [archiveResult, setArchiveResult] = useState<BulkArchiveResult>();
  const results = useQuery({
    queryKey: ['remnants', 'all', search],
    queryFn: () => listAllRemnants(search),
  });
  const exporting = useMutation({
    mutationFn: exportAllRemnants,
    onSuccess: () => message.success('全部余料已导出'),
    onError: async (error) => message.error(await describeRemnantErrorAsync(error, '余料导出失败')),
  });
  const archiving = useMutation({
    mutationFn: bulkArchiveRemnants,
    onSuccess: async (result) => {
      setArchiveResult(result);
      setSelectedIds(result.failed.map((item) => item.remnant_id));
      await queryClient.invalidateQueries({ queryKey: ['remnants', 'all'] });
    },
    onError: (error) => message.error(describeRemnantError(error, '批量归档失败')),
  });
  const deleting = useMutation({
    mutationFn: deleteArchivedRemnant,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['remnants'] });
      message.success('已删除归档余料；该图纸现在可以重新提交');
    },
    onError: (error) => message.error(describeRemnantError(error, '删除归档余料失败')),
  });

  return <div className="remnant-tab-stack">
    <Card bordered={false} className="remnant-search-card">
      <Form
        form={form}
        layout="vertical"
        initialValues={{ statuses: activeStatuses, sort: 'created_desc' }}
        onFinish={(values) => {
          setSelectedIds([]);
          setSearch({
            materialId: values.materialId,
            thicknessMm: values.thicknessMm ? String(values.thicknessMm) : undefined,
            statuses: values.statuses?.length ? values.statuses : (showHistory ? allStatuses : activeStatuses),
            project: values.project?.trim() || undefined,
            projectSecondary: values.projectSecondary?.trim() || undefined,
            storageLocation: values.storageLocation?.trim() || undefined,
            remark1: values.remark1?.trim() || undefined,
            remark2: values.remark2?.trim() || undefined,
            part: values.part?.trim() || undefined,
            sort: values.sort,
            page: 1,
          });
        }}
      >
        <div className="remnant-global-search-grid">
          <Form.Item name="materialId" label="材质"><Select allowClear showSearch optionFilterProp="label" options={materials.map((item) => ({ value: item.id, label: item.code }))} /></Form.Item>
          <Form.Item name="thicknessMm" label="厚度（mm）"><InputNumber min={0.001} precision={3} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="statuses" label="库存状态"><Select mode="multiple" options={showHistory ? statusOptions : statusOptions.slice(0, 2)} maxTagCount="responsive" /></Form.Item>
          <Form.Item name="project" label="项目编号一"><Input aria-label="项目编号一筛选" allowClear /></Form.Item>
          <Form.Item name="projectSecondary" label="项目编号二"><Input aria-label="项目编号二筛选" allowClear /></Form.Item>
          <Form.Item name="storageLocation" label="库存位置"><Input aria-label="库存位置筛选" allowClear /></Form.Item>
          <Form.Item name="remark1" label="备注一"><Input aria-label="备注一筛选" allowClear /></Form.Item>
          <Form.Item name="remark2" label="备注二"><Input aria-label="备注二筛选" allowClear /></Form.Item>
          <Form.Item name="part" label="零件编号"><Input aria-label="零件编号筛选" allowClear /></Form.Item>
          <Form.Item name="sort" label="排序"><Select options={sortOptions} /></Form.Item>
          <Form.Item className="remnant-global-search-action"><Button type="primary" htmlType="submit" icon={<SearchOutlined />}>查询全部余料</Button></Form.Item>
        </div>
      </Form>
    </Card>
    <Card bordered={false} className="remnant-results-card">
      <div className="remnant-section-heading">
        <div>
          <Typography.Title level={4}>全部余料</Typography.Title>
          <Typography.Text type="secondary">共 {results.data?.pagination.total ?? 0} 张，{showHistory ? '包含全部库存状态' : '默认隐藏已使用和已归档余料'}</Typography.Text>
        </div>
        <Space wrap>
          <Space>
            <Typography.Text>显示历史余料</Typography.Text>
            <Switch
              aria-label="显示历史余料"
              checked={showHistory}
              onChange={(checked) => {
                const statuses = checked ? allStatuses : activeStatuses;
                setShowHistory(checked);
                setSelectedIds([]);
                form.setFieldValue('statuses', statuses);
                setSearch((current) => ({ ...current, statuses, page: 1 }));
              }}
            />
          </Space>
          <Popconfirm
            title={`确认归档选中的 ${selectedIds.length} 张余料？`}
            description="符合权限和库存状态的余料会被归档，其余余料会保留并显示原因。"
            onConfirm={() => archiving.mutate(selectedIds)}
          >
            <Button danger icon={<DeleteOutlined />} disabled={!selectedIds.length} loading={archiving.isPending}>批量归档</Button>
          </Popconfirm>
          <Button icon={<DownloadOutlined />} loading={exporting.isPending} onClick={() => exporting.mutate()}>导出全部余料</Button>
        </Space>
      </div>
      {archiveResult && <Alert
        closable
        showIcon
        type={archiveResult.failed.length ? 'warning' : 'success'}
        message={`已归档 ${archiveResult.archived.length} 张，${archiveResult.failed.length} 张未处理`}
        description={archiveResult.failed.map((item) => <div key={item.remnant_id}>余料 #{item.remnant_id}：{operatorErrorMessage(undefined, item.message, '当前状态不能归档，请刷新后核对。')}</div>)}
        onClose={() => setArchiveResult(undefined)}
        style={{ marginBottom: 16 }}
      />}
      <Table<Remnant>
        rowKey="id"
        scroll={{ x: 1900 }}
        loading={results.isFetching}
        dataSource={results.data?.data ?? []}
        rowSelection={{
          selectedRowKeys: selectedIds,
          preserveSelectedRowKeys: true,
          onChange: (keys) => setSelectedIds(keys.map(Number)),
          getCheckboxProps: (row) => ({
            disabled: row.status !== 'available' || (!isAdmin && row.imported_by !== currentUserId),
            'aria-label': `选择余料 ${row.id}`,
          }),
        }}
        pagination={{
          current: search.page,
          pageSize: 20,
          total: results.data?.pagination.total ?? 0,
          showSizeChanger: false,
          onChange: (page) => setSearch((current) => ({ ...current, page })),
        }}
        columns={[
          { title: '余料编号', dataIndex: 'id', width: 100 },
          { title: '状态', dataIndex: 'status', width: 100, render: (status) => <StatusTag status={status} /> },
          { title: '材质', dataIndex: 'material_code', width: 120 },
          { title: '厚度', dataIndex: 'thickness_mm', width: 110, render: (value) => `${value} mm` },
          { title: '项目编号一', dataIndex: 'project_no', ellipsis: true },
          { title: '项目编号二', dataIndex: 'project_no_secondary', ellipsis: true, render: (value) => value || '—' },
          { title: '库存位置', dataIndex: 'storage_location', ellipsis: true, render: (value) => value || '—' },
          { title: '备注一', dataIndex: 'remark_1', ellipsis: true, render: (value) => value || '—' },
          { title: '备注二', dataIndex: 'remark_2', ellipsis: true, render: (value) => value || '—' },
          { title: '零件编号', dataIndex: 'parts', ellipsis: true, render: (parts: string[]) => parts.join('、') },
          { title: '原始文件', dataIndex: 'source_name', ellipsis: true },
          { title: '更新时间', dataIndex: 'updated_at', width: 190, render: (value) => new Date(value).toLocaleString('zh-CN') },
          { title: '操作', key: 'actions', width: 170, fixed: 'right', render: (_, row) => <Space>
            <Button type="link" icon={<EyeOutlined />} onClick={() => onOpenDetail(row.id)}>详情</Button>
            {row.status === 'archived' && (isAdmin || row.imported_by === currentUserId) && (
              <Popconfirm
                title="确认永久删除这张已归档余料？"
                description="审计日志和原图保留，删除后同一图纸可以重新提交。"
                onConfirm={() => deleting.mutate(row.id)}
              >
                <Button danger type="link" loading={deleting.isPending && deleting.variables === row.id}>删除</Button>
              </Popconfirm>
            )}
          </Space> },
        ]}
      />
    </Card>
  </div>;
}
