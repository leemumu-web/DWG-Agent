import { useState } from 'react';
import { DownloadOutlined, EyeOutlined, SearchOutlined } from '@ant-design/icons';
import { App, Button, Card, Form, Input, InputNumber, Select, Space, Table, Typography } from 'antd';
import { useMutation, useQuery } from '@tanstack/react-query';
import { describeApiError } from '../../shared/api';
import { exportAllRemnants, listAllRemnants } from './api';
import { StatusTag } from './RemnantDetailDrawer';
import type { Remnant, RemnantGlobalSearch, RemnantMaterial, RemnantStatus } from './types';

interface Props {
  materials: RemnantMaterial[];
  onOpenDetail: (id: number) => void;
}

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
  statuses: allStatuses,
  sort: 'created_desc',
  page: 1,
};

export function RemnantGlobalPanel({ materials, onOpenDetail }: Props) {
  const { message } = App.useApp();
  const [search, setSearch] = useState<RemnantGlobalSearch>(initialSearch);
  const results = useQuery({
    queryKey: ['remnants', 'all', search],
    queryFn: () => listAllRemnants(search),
  });
  const exporting = useMutation({
    mutationFn: exportAllRemnants,
    onSuccess: () => message.success('全部余料已导出'),
    onError: (error) => message.error(describeApiError(error, '余料导出失败')),
  });

  return <div className="remnant-tab-stack">
    <Card bordered={false} className="remnant-search-card">
      <Form
        layout="vertical"
        initialValues={{ statuses: allStatuses, sort: 'created_desc' }}
        onFinish={(values) => setSearch({
          materialId: values.materialId,
          thicknessMm: values.thicknessMm ? String(values.thicknessMm) : undefined,
          statuses: values.statuses?.length ? values.statuses : allStatuses,
          project: values.project?.trim() || undefined,
          part: values.part?.trim() || undefined,
          sort: values.sort,
          page: 1,
        })}
      >
        <div className="remnant-global-search-grid">
          <Form.Item name="materialId" label="材质"><Select allowClear showSearch optionFilterProp="label" options={materials.map((item) => ({ value: item.id, label: item.code }))} /></Form.Item>
          <Form.Item name="thicknessMm" label="厚度（mm）"><InputNumber min={0.001} precision={3} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="statuses" label="库存状态"><Select mode="multiple" options={statusOptions} maxTagCount="responsive" /></Form.Item>
          <Form.Item name="project" label="项目编号"><Input aria-label="项目编号筛选" allowClear /></Form.Item>
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
          <Typography.Text type="secondary">共 {results.data?.pagination.total ?? 0} 张，包含全部库存状态</Typography.Text>
        </div>
        <Button icon={<DownloadOutlined />} loading={exporting.isPending} onClick={() => exporting.mutate()}>导出全部余料</Button>
      </div>
      <Table<Remnant>
        rowKey="id"
        loading={results.isFetching}
        dataSource={results.data?.data ?? []}
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
          { title: '项目编号', dataIndex: 'project_no', ellipsis: true },
          { title: '零件编号', dataIndex: 'parts', ellipsis: true, render: (parts: string[]) => parts.join('、') },
          { title: '原始文件', dataIndex: 'source_name', ellipsis: true },
          { title: '更新时间', dataIndex: 'updated_at', width: 190, render: (value) => new Date(value).toLocaleString('zh-CN') },
          { title: '操作', key: 'actions', width: 90, render: (_, row) => <Space><Button type="link" icon={<EyeOutlined />} onClick={() => onOpenDetail(row.id)}>详情</Button></Space> },
        ]}
      />
    </Card>
  </div>;
}
