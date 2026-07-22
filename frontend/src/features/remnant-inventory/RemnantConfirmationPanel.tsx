import { useMemo, useState } from 'react';
import { Alert, App, Button, Card, Descriptions, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography } from 'antd';
import { CheckOutlined, EditOutlined, EyeOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { DxfPreviewModal } from '../files';
import { bulkApplyThickness, confirmRemnantImportItems, updateRemnantImportItem } from './api';
import type { RemnantImportBatch, RemnantImportItem, RemnantMaterial } from './types';

interface Props { batch: RemnantImportBatch; materials: RemnantMaterial[] }

export function RemnantConfirmationPanel({ batch, materials }: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<React.Key[]>([]);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkThickness, setBulkThickness] = useState<number>();
  const [editing, setEditing] = useState<RemnantImportItem>();
  const [preview, setPreview] = useState<RemnantImportItem>();
  const [form] = Form.useForm();
  const rows = useMemo(() => batch.items.filter((item) => ['pending_confirmation', 'confirmed'].includes(item.status)), [batch.items]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['remnant-import-batch', batch.id] });

  const bulk = useMutation({
    mutationFn: () => bulkApplyThickness(batch.id, selected.map(Number), String(bulkThickness)),
    onSuccess: async () => { setBulkOpen(false); await refresh(); message.success('已批量填写厚度'); },
  });
  const save = useMutation({
    mutationFn: (values: { thickness_mm: number; material_id: number; project_no: string; partsText: string }) => updateRemnantImportItem(editing!.id, {
      thickness_mm: String(values.thickness_mm), material_id: values.material_id,
      project_no: values.project_no, parts: values.partsText.split(/[、,，\n]/).map((value) => value.trim()).filter(Boolean),
    }),
    onSuccess: async () => { setEditing(undefined); await refresh(); message.success('图纸信息已保存'); },
  });
  const confirm = useMutation({
    mutationFn: () => confirmRemnantImportItems(selected.map(Number)),
    onSuccess: async (result) => {
      await refresh();
      setSelected(result.invalid.map((item) => item.item_id));
      if (result.invalid.length) message.warning(`已确认 ${result.confirmed.length} 张，${result.invalid.length} 张需补充字段`);
      else message.success(`已确认 ${result.confirmed.length + result.already_confirmed.length} 张余料`);
    },
  });

  const edit = (item: RemnantImportItem) => {
    setEditing(item);
    form.setFieldsValue({
      thickness_mm: item.thickness_mm ? Number(item.thickness_mm) : undefined,
      material_id: item.material_id ?? undefined,
      project_no: item.project_no ?? item.project_candidates[0]?.value,
      partsText: (item.parts.length ? item.parts : item.part_candidates.map((candidate) => candidate.value)).join('、'),
    });
  };
  return (
    <Card bordered={false} className="remnant-confirm-card">
      <div className="remnant-section-heading">
        <div><Typography.Title level={4}>解析确认</Typography.Title><Typography.Text type="secondary">选择图纸后可批量填写厚度；确认只处理选中的有效行。</Typography.Text></div>
        <Space>
          <Button disabled={!selected.length} onClick={() => setBulkOpen(true)}>批量填写厚度</Button>
          <Button type="primary" icon={<CheckOutlined />} disabled={!selected.length} loading={confirm.isPending} onClick={() => confirm.mutate()}>确认选中项</Button>
        </Space>
      </div>
      <Table<RemnantImportItem>
        rowKey="id"
        dataSource={rows}
        pagination={false}
        rowSelection={{ selectedRowKeys: selected, onChange: setSelected, getCheckboxProps: (row) => ({ disabled: row.status === 'confirmed' }) }}
        columns={[
          { title: '原始文件', dataIndex: 'original_name', ellipsis: true },
          { title: '厚度', dataIndex: 'thickness_mm', width: 100, render: (value) => value ? `${value} mm` : <Tag color="error">待填写</Tag> },
          { title: '材质候选', key: 'material', render: (_, row) => row.material_candidates.map((item) => item.value).join(' / ') || '—' },
          { title: '项目编号', key: 'project', render: (_, row) => row.project_no ?? row.project_candidates[0]?.value ?? '—' },
          { title: '零件数', key: 'parts', width: 90, render: (_, row) => row.parts.length || row.part_candidates.length },
          { title: '操作', key: 'actions', width: 160, render: (_, row) => <Space><Button type="link" icon={<EyeOutlined />} disabled={!row.dxf_file_id} onClick={() => setPreview(row)}>预览</Button><Button type="link" icon={<EditOutlined />} disabled={row.status === 'confirmed'} onClick={() => edit(row)}>编辑</Button></Space> },
        ]}
      />
      <Modal title="批量填写厚度" open={bulkOpen} onCancel={() => setBulkOpen(false)} onOk={() => bulk.mutate()} okButtonProps={{ disabled: !bulkThickness }} confirmLoading={bulk.isPending}>
        <InputNumber aria-label="批量厚度" min={0.001} precision={3} value={bulkThickness} onChange={(value) => setBulkThickness(value ?? undefined)} addonAfter="mm" style={{ width: '100%' }} />
      </Modal>
      <Modal title={editing ? `确认 ${editing.original_name}` : '确认图纸'} open={Boolean(editing)} width={760} onCancel={() => setEditing(undefined)} onOk={() => form.submit()} confirmLoading={save.isPending}>
        {editing && <div className="remnant-confirm-editor">
          <div>
            <Button icon={<EyeOutlined />} disabled={!editing.dxf_file_id} onClick={() => setPreview(editing)}>打开图形预览</Button>
            {editing.warnings.map((warning) => <Alert key={warning.code} type="warning" showIcon title={warning.code} description={warning.message} style={{ marginTop: 12 }} />)}
            <Descriptions size="small" column={1} style={{ marginTop: 16 }} items={[
              { key: 'material', label: '材质证据', children: editing.material_candidates.flatMap((item) => item.evidence).map((item) => `${item.layer}: ${item.raw_text}`).join('；') || '无' },
              { key: 'project', label: '项目证据', children: editing.project_candidates.flatMap((item) => item.evidence).map((item) => item.raw_text).join('；') || '无' },
            ]} />
          </div>
          <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
            <Form.Item name="thickness_mm" label="厚度（mm）" rules={[{ required: true }]}><InputNumber min={0.001} precision={3} style={{ width: '100%' }} /></Form.Item>
            <Form.Item name="material_id" label="标准材质" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" options={materials.map((item) => ({ value: item.id, label: item.code }))} /></Form.Item>
            <Form.Item name="project_no" label="项目编号" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="partsText" label="零件编号（逗号、顿号或换行分隔）" rules={[{ required: true }]}><Input.TextArea rows={4} /></Form.Item>
          </Form>
        </div>}
      </Modal>
      <DxfPreviewModal fileId={preview?.dxf_file_id ?? null} fileName={preview?.original_name ?? ''} open={Boolean(preview)} onClose={() => setPreview(undefined)} />
    </Card>
  );
}

