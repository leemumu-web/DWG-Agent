import { useEffect } from 'react';
import { App, Form, Input, InputNumber, Modal, Select } from 'antd';
import { useMutation } from '@tanstack/react-query';
import { updateRemnant } from './api';
import { describeRemnantError } from './errors';
import type { Remnant, RemnantMaterial } from './types';

interface Props { remnant?: Remnant; materials: RemnantMaterial[]; open: boolean; onClose: () => void; onSaved: (row: Remnant) => void }
interface Values {
  thickness_mm: number;
  material_id: number;
  project_no: string;
  project_no_secondary?: string;
  storage_location?: string;
  remark_1?: string;
  remark_2?: string;
  partsText: string;
}

export function RemnantEditModal({ remnant, materials, open, onClose, onSaved }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<Values>();
  useEffect(() => {
    if (remnant) form.setFieldsValue({
      thickness_mm: Number(remnant.thickness_mm),
      material_id: remnant.material_id,
      project_no: remnant.project_no,
      project_no_secondary: remnant.project_no_secondary ?? '',
      storage_location: remnant.storage_location ?? '',
      remark_1: remnant.remark_1 ?? '',
      remark_2: remnant.remark_2 ?? '',
      partsText: remnant.parts.join('、'),
    });
  }, [form, remnant]);
  const save = useMutation({
    mutationFn: (values: Values) => updateRemnant(remnant!.id, {
      thickness_mm: String(values.thickness_mm), material_id: values.material_id,
      project_no: values.project_no,
      project_no_secondary: values.project_no_secondary ?? '',
      storage_location: values.storage_location ?? '',
      remark_1: values.remark_1 ?? '',
      remark_2: values.remark_2 ?? '',
      parts: values.partsText.split(/[、,，\n]/).map((value) => value.trim()).filter(Boolean),
    }),
    onSuccess: onSaved,
    onError: (error) => message.error(describeRemnantError(error, '余料信息保存失败')),
  });
  return <Modal title="编辑余料信息" width={680} open={open} onCancel={onClose} onOk={() => form.submit()} confirmLoading={save.isPending}>
    <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
      <Form.Item name="thickness_mm" label="厚度（mm）" rules={[{ required: true }]}><InputNumber min={0.001} precision={3} style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="material_id" label="标准材质" rules={[{ required: true }]}><Select options={materials.filter((item) => item.enabled).map((item) => ({ value: item.id, label: item.code }))} /></Form.Item>
      <Form.Item name="project_no" label="项目编号一" rules={[{ required: true }]}><Input /></Form.Item>
      <Form.Item name="project_no_secondary" label="项目编号二（可空）"><Input allowClear /></Form.Item>
      <Form.Item name="storage_location" label="库存位置（可空）"><Input allowClear /></Form.Item>
      <Form.Item name="remark_1" label="备注一（可空）"><Input.TextArea rows={2} allowClear /></Form.Item>
      <Form.Item name="remark_2" label="备注二（可空）"><Input.TextArea rows={2} allowClear /></Form.Item>
      <Form.Item name="partsText" label="零件编号" rules={[{ required: true }]}><Input.TextArea rows={4} /></Form.Item>
    </Form>
  </Modal>;
}
