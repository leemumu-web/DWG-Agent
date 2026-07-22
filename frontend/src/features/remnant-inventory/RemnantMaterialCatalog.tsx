import { useState } from 'react';
import { App, Button, Card, Form, Input, Modal, Popconfirm, Space, Switch, Table, Tag, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { createRemnantMaterial, replaceRemnantMaterialAliases, updateRemnantMaterial } from './api';
import type { RemnantMaterial } from './types';

interface Props { materials: RemnantMaterial[]; loading: boolean }
interface Values { code: string; family_code: string; aliasesText: string }

export function RemnantMaterialCatalog({ materials, loading }: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<RemnantMaterial | null>();
  const [form] = Form.useForm<Values>();
  const save = useMutation({
    mutationFn: async (values: Values) => {
      const material = editing
        ? await updateRemnantMaterial(editing.id, { family_code: values.family_code })
        : await createRemnantMaterial({ code: values.code, family_code: values.family_code });
      const aliases = values.aliasesText.split(/[、,，\n]/).map((value) => value.trim()).filter(Boolean);
      await replaceRemnantMaterialAliases(material.id, aliases);
    },
    onSuccess: async () => {
      setEditing(undefined);
      await queryClient.invalidateQueries({ queryKey: ['remnant-materials'] });
      message.success('材质目录已保存');
    },
  });
  const toggle = useMutation({
    mutationFn: (row: RemnantMaterial) => updateRemnantMaterial(row.id, { enabled: !row.enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['remnant-materials'] }),
  });
  const open = (row: RemnantMaterial | null) => {
    setEditing(row);
    form.setFieldsValue({
      code: row?.code ?? '',
      family_code: row?.family_code ?? '',
      aliasesText: row?.aliases.join('、') ?? '',
    });
  };
  return <Card bordered={false} className="remnant-results-card">
    <div className="remnant-section-heading">
      <div><Typography.Title level={4}>标准材质目录</Typography.Title><Typography.Text type="secondary">维护完整牌号、同系列标识和图纸解析别名。</Typography.Text></div>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => open(null)}>新增材质</Button>
    </div>
    <Table<RemnantMaterial> rowKey="id" loading={loading} dataSource={materials} pagination={false} columns={[
      { title: '标准牌号', dataIndex: 'code', width: 180 },
      { title: '同系列标识', dataIndex: 'family_code', width: 180 },
      { title: '解析别名', dataIndex: 'aliases', render: (values: string[]) => <Space wrap>{values.length ? values.map((value) => <Tag key={value}>{value}</Tag>) : '—'}</Space> },
      { title: '启用', dataIndex: 'enabled', width: 90, render: (enabled) => <Switch checked={enabled} disabled /> },
      { title: '操作', key: 'actions', width: 180, render: (_, row) => <Space><Button type="link" onClick={() => open(row)}>编辑</Button><Popconfirm title={row.enabled ? '停用该材质？' : '重新启用该材质？'} onConfirm={() => toggle.mutate(row)}><Button type="link" danger={row.enabled}>{row.enabled ? '停用' : '启用'}</Button></Popconfirm></Space> },
    ]} />
    <Modal title={editing ? `编辑 ${editing.code}` : '新增标准材质'} open={editing !== undefined} onCancel={() => setEditing(undefined)} onOk={() => form.submit()} confirmLoading={save.isPending}>
      <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
        <Form.Item name="code" label="标准完整牌号" rules={[{ required: true }]}><Input disabled={Boolean(editing)} placeholder="例如 Q235B-Z15" /></Form.Item>
        <Form.Item name="family_code" label="同系列标识" rules={[{ required: true }]}><Input placeholder="例如 Q235" /></Form.Item>
        <Form.Item name="aliasesText" label="解析别名（逗号、顿号或换行分隔）"><Input.TextArea rows={4} /></Form.Item>
      </Form>
    </Modal>
  </Card>;
}
