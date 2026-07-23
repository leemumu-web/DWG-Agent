import { useState } from 'react';
import { App, Button, Card, Form, Input, Modal, Space, Switch, Table, Tag, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { createRemnantMaterial, replaceRemnantMaterialAliases, updateRemnantMaterial } from './api';
import { describeRemnantError } from './errors';
import type { RemnantMaterial } from './types';

interface Props { materials: RemnantMaterial[]; loading: boolean }
interface Values { code: string; family_code: string; aliasesText: string }

export function RemnantMaterialCatalog({ materials, loading }: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<RemnantMaterial | null>();
  const [pendingToggle, setPendingToggle] = useState<RemnantMaterial>();
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
    onSuccess: async () => {
      setPendingToggle(undefined);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['remnant-materials'], exact: true }),
        queryClient.invalidateQueries({ queryKey: ['remnant-materials', 'all'], exact: true }),
      ]);
      message.success('材质启用状态已更新');
    },
    onError: (error) => {
      setPendingToggle(undefined);
      message.error(describeRemnantError(error, '材质启用状态更新失败'));
    },
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
      { title: '启用', dataIndex: 'enabled', width: 90, render: (enabled, row) => <Switch aria-label={`${row.code} 启用状态`} checked={enabled} loading={toggle.isPending && pendingToggle?.id === row.id} onChange={() => setPendingToggle(row)} /> },
      { title: '操作', key: 'actions', width: 100, render: (_, row) => <Button type="link" onClick={() => open(row)}>编辑</Button> },
    ]} />
    <Modal
      title={pendingToggle ? `${pendingToggle.enabled ? '停用' : '重新启用'} ${pendingToggle.code}？` : '修改材质启用状态'}
      open={Boolean(pendingToggle)}
      onCancel={() => setPendingToggle(undefined)}
      onOk={() => pendingToggle && toggle.mutate(pendingToggle)}
      confirmLoading={toggle.isPending}
      cancelButtonProps={{ disabled: toggle.isPending }}
      closable={!toggle.isPending}
      maskClosable={!toggle.isPending}
    >
      <Typography.Text>
        {pendingToggle?.enabled ? '停用后，导入余料时将不能继续选择该材质。' : '启用后，该材质将立即恢复为可选状态。'}
      </Typography.Text>
    </Modal>
    <Modal title={editing ? `编辑 ${editing.code}` : '新增标准材质'} open={editing !== undefined} onCancel={() => setEditing(undefined)} onOk={() => form.submit()} confirmLoading={save.isPending}>
      <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
        <Form.Item name="code" label="标准完整牌号" rules={[{ required: true }]}><Input disabled={Boolean(editing)} placeholder="例如 Q235B-Z15" /></Form.Item>
        <Form.Item name="family_code" label="同系列标识" rules={[{ required: true }]}><Input placeholder="例如 Q235" /></Form.Item>
        <Form.Item name="aliasesText" label="解析别名（逗号、顿号或换行分隔）"><Input.TextArea rows={4} /></Form.Item>
      </Form>
    </Modal>
  </Card>;
}
