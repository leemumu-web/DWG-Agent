import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, App, AutoComplete, Button, Card, Descriptions, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography } from 'antd';
import { CheckOutlined, CloseCircleOutlined, EditOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { DxfPreviewModal } from '../files';
import {
  bulkApplyProject,
  bulkApplyOptionalMetadata,
  bulkApplyThickness,
  cancelRemnantImportItem,
  confirmRemnantImportItems,
  resolveOrCreateRemnantMaterial,
  retryRemnantImportItem,
  updateRemnantImportItem,
} from './api';
import { describeRemnantCode, describeRemnantError, warningTitle } from './errors';
import type { RemnantImportBatch, RemnantImportItem, RemnantMaterial } from './types';

interface Props { batch: RemnantImportBatch; materials: RemnantMaterial[] }

function normalizeMaterialCode(value: string): string {
  return value.normalize('NFKC').trim().toUpperCase().replace(/\s+/g, ' ');
}

function uniqueMaterialCandidate(item: RemnantImportItem): string | undefined {
  const candidates = [...new Set(item.material_candidates.map((candidate) => normalizeMaterialCode(candidate.value)).filter(Boolean))];
  return candidates.length === 1 ? candidates[0] : undefined;
}

function uniqueProjectCandidate(item: RemnantImportItem): string | undefined {
  const candidates = [...new Set(item.project_candidates.map((candidate) => candidate.value.trim()).filter(Boolean))];
  return candidates.length === 1 ? candidates[0] : undefined;
}

export function RemnantConfirmationPanel({ batch, materials }: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<React.Key[]>([]);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkThickness, setBulkThickness] = useState<number>();
  const [bulkProjectOpen, setBulkProjectOpen] = useState(false);
  const [bulkProject, setBulkProject] = useState('');
  const [bulkMetadataOpen, setBulkMetadataOpen] = useState(false);
  const [bulkProjectSecondary, setBulkProjectSecondary] = useState('');
  const [bulkStorageLocation, setBulkStorageLocation] = useState('');
  const [editing, setEditing] = useState<RemnantImportItem>();
  const editingItemIdRef = useRef<number | undefined>(undefined);
  const editorGenerationRef = useRef(0);
  const materialCodeRef = useRef('');
  const [materialCode, setMaterialCode] = useState('');
  const [preview, setPreview] = useState<RemnantImportItem>();
  const [validationErrors, setValidationErrors] = useState<Record<number, string>>({});
  const [form] = Form.useForm();
  const selectedMaterialId = Form.useWatch('material_id', form);
  const isAuto = batch.import_mode === 'auto';
  const rows = useMemo(
    () => isAuto ? batch.items : batch.items.filter((item) => ['pending_confirmation', 'confirmed'].includes(item.status)),
    [batch.items, isAuto],
  );
  const seenItemIds = useRef<Set<number>>(new Set());
  const selectionBatchId = useRef<number | undefined>(undefined);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['remnant-import-batch', batch.id] });
  const unmatchedMaterialCodes = useMemo(() => {
    if (!editing) return [];
    const catalog = new Set(materials.map((material) => normalizeMaterialCode(material.code)));
    return [...new Set(
      editing.material_candidates
        .map((candidate) => normalizeMaterialCode(candidate.value))
        .filter((candidate) => candidate && !catalog.has(candidate)),
    )];
  }, [editing, materials]);

  useEffect(() => {
    const pending = batch.items.filter((item) => item.status === 'pending_confirmation');
    if (selectionBatchId.current !== batch.id) {
      selectionBatchId.current = batch.id;
      seenItemIds.current = new Set(batch.items.map((item) => item.id));
      setSelected(pending.map((item) => item.id));
      return;
    }
    const newPending = pending.filter((item) => !seenItemIds.current.has(item.id)).map((item) => item.id);
    batch.items.forEach((item) => seenItemIds.current.add(item.id));
    setSelected((current) => [
      ...current.filter((id) => pending.some((item) => item.id === Number(id))),
      ...newPending,
    ]);
  }, [batch.id, batch.items]);

  const bulk = useMutation({
    mutationFn: () => bulkApplyThickness(batch.id, selected.map(Number), String(bulkThickness)),
    onSuccess: async () => { setBulkOpen(false); await refresh(); message.success('已批量填写厚度'); },
    onError: (error) => message.error(describeRemnantError(error, '批量填写厚度失败')),
  });
  const applyProject = useMutation({
    mutationFn: () => bulkApplyProject(batch.id, selected.map(Number), bulkProject.trim()),
    onSuccess: async () => {
      setBulkProjectOpen(false);
      await refresh();
      message.success('已批量设置项目编号');
    },
    onError: (error) => message.error(describeRemnantError(error, '批量设置项目编号失败')),
  });
  const applyMetadata = useMutation({
    mutationFn: () => bulkApplyOptionalMetadata(batch.id, selected.map(Number), {
      project_no_secondary: bulkProjectSecondary,
      storage_location: bulkStorageLocation,
    }),
    onSuccess: async () => {
      setBulkMetadataOpen(false);
      await refresh();
      message.success('已批量填写项目编号二和库存位置');
    },
    onError: (error) => message.error(describeRemnantError(error, '批量填写附加信息失败')),
  });
  const itemAction = useMutation({
    mutationFn: async ({ action, itemId }: { action: 'retry' | 'cancel'; itemId: number }) => {
      if (action === 'retry') await retryRemnantImportItem(itemId);
      else await cancelRemnantImportItem(itemId);
    },
    onSuccess: async (_value, variables) => {
      await refresh();
      message.success(variables.action === 'retry' ? '已重新提交解析' : '已移除该图纸');
    },
    onError: (error) => message.error(describeRemnantError(error, '图纸操作失败')),
  });
  const save = useMutation({
    mutationFn: (values: {
      thickness_mm: number;
      material_id: number;
      project_no: string;
      project_no_secondary?: string;
      storage_location?: string;
      remark_1?: string;
      remark_2?: string;
      parts: string[];
    }) => updateRemnantImportItem(editing!.id, {
      thickness_mm: String(values.thickness_mm), material_id: values.material_id,
      project_no: values.project_no,
      project_no_secondary: values.project_no_secondary ?? '',
      storage_location: values.storage_location ?? '',
      remark_1: values.remark_1 ?? '',
      remark_2: values.remark_2 ?? '',
      parts: [...new Set(values.parts.map((value) => value.trim()).filter(Boolean))],
    }),
    onSuccess: async () => { editingItemIdRef.current = undefined; setEditing(undefined); await refresh(); message.success('图纸信息已保存'); },
    onError: (error) => message.error(describeRemnantError(error, '图纸信息保存失败')),
  });
  const createDetectedMaterial = useMutation({
    mutationFn: ({ itemId, code }: { itemId: number; code: string; generation: number }) =>
      resolveOrCreateRemnantMaterial(itemId, code),
    onSuccess: (result, variables) => {
      queryClient.setQueryData<RemnantMaterial[]>(['remnant-materials'], (current = []) =>
        current.some((row) => row.id === result.material.id)
          ? current
          : [...current, result.material],
      );
      if (
        editorGenerationRef.current === variables.generation
        && editingItemIdRef.current === variables.itemId
        && normalizeMaterialCode(materialCodeRef.current) === variables.code
      ) {
        form.setFieldValue('material_id', result.material.id);
        message.success(result.created ? '材质已创建并选中' : '已选中现有材质');
      } else {
        message.success(result.created ? '材质已创建' : '材质已存在');
      }
    },
    onError: (error) => message.error(describeRemnantError(error, '材质创建失败')),
  });
  const confirm = useMutation({
    mutationFn: () => confirmRemnantImportItems(selected.map(Number)),
    onSuccess: async (result) => {
      await refresh();
      setSelected(result.invalid.map((item) => item.item_id));
      setValidationErrors(Object.fromEntries(result.invalid.map((item) => [item.item_id, item.code])));
      if (result.invalid.length) message.warning(`已确认 ${result.confirmed.length} 张，${result.invalid.length} 张需补充字段`);
      else message.success(`已确认 ${result.confirmed.length + result.already_confirmed.length} 张余料`);
    },
    onError: (error) => message.error(describeRemnantError(error, '图纸确认失败')),
  });

  const edit = (item: RemnantImportItem) => {
    editorGenerationRef.current += 1;
    const detectedCode = uniqueMaterialCandidate(item);
    const detectedMaterial = detectedCode
      ? materials.find((material) => normalizeMaterialCode(material.code) === detectedCode)
      : undefined;
    const unmatched = [...new Set(
      item.material_candidates
        .map((candidate) => normalizeMaterialCode(candidate.value))
        .filter((candidate) => candidate && !materials.some((material) => normalizeMaterialCode(material.code) === candidate)),
    )];
    const initialMaterialCode = unmatched.length === 1 ? unmatched[0] : '';
    editingItemIdRef.current = item.id;
    materialCodeRef.current = initialMaterialCode;
    setMaterialCode(initialMaterialCode);
    setEditing(item);
    form.setFieldsValue({
      thickness_mm: item.thickness_mm ? Number(item.thickness_mm) : undefined,
      material_id: item.material_id ?? detectedMaterial?.id,
      project_no: item.project_no ?? uniqueProjectCandidate(item),
      project_no_secondary: item.project_no_secondary ?? '',
      storage_location: item.storage_location ?? '',
      remark_1: item.remark_1 ?? '',
      remark_2: item.remark_2 ?? '',
      parts: [...new Set(
        (item.parts.length ? item.parts : item.part_candidates.map((candidate) => candidate.value))
          .map((value) => value.trim())
          .filter(Boolean),
      )],
    });
  };
  const closeEditor = () => {
    editorGenerationRef.current += 1;
    editingItemIdRef.current = undefined;
    materialCodeRef.current = '';
    setMaterialCode('');
    setEditing(undefined);
  };
  return (
    <Card bordered={false} className="remnant-confirm-card">
      <div className="remnant-section-heading">
        <div><Typography.Title level={4}>解析确认</Typography.Title><Typography.Text type="secondary">{isAuto ? '默认选择全部可确认图纸；可批量设置项目、逐行核对后一次导入。' : '选择图纸后可批量填写厚度；确认只处理选中的有效行。'}</Typography.Text></div>
        <Space>
          {isAuto && <Button disabled={!selected.length} onClick={() => {
            setBulkProject(batch.default_project_no ?? '');
            setBulkProjectOpen(true);
          }}>批量设置项目编号</Button>}
          <Button disabled={!selected.length} onClick={() => {
            setBulkProjectSecondary('');
            setBulkStorageLocation('');
            setBulkMetadataOpen(true);
          }}>批量填写附加信息</Button>
          <Button disabled={!selected.length} onClick={() => setBulkOpen(true)}>批量填写厚度</Button>
          <Button type="primary" icon={<CheckOutlined />} disabled={!selected.length} loading={confirm.isPending} onClick={() => confirm.mutate()}>{isAuto ? '一键导入选中的有效项' : '确认选中项'}</Button>
        </Space>
      </div>
      <Table<RemnantImportItem>
        rowKey="id"
        dataSource={rows}
        pagination={false}
        scroll={{ x: isAuto ? 2100 : 1600 }}
        rowSelection={{ fixed: true, selectedRowKeys: selected, onChange: setSelected, getCheckboxProps: (row) => ({ disabled: row.status !== 'pending_confirmation' }) }}
        columns={[
          { title: '文件名', dataIndex: 'original_name', width: 220, ellipsis: true },
          ...(isAuto ? [
            { title: '相对路径', dataIndex: 'source_relative_path', width: 240, ellipsis: true, render: (value: string | null) => value ?? '—' },
            { title: '项目编号', key: 'project', width: 180, ellipsis: true, render: (_: unknown, row: RemnantImportItem) => row.project_no ?? batch.default_project_no ?? '—' },
          ] : []),
          { title: '厚度', dataIndex: 'thickness_mm', width: 100, render: (value) => value ? `${value} mm` : <Tag color="error">待填写</Tag> },
          { title: isAuto ? '材质' : '材质候选', key: 'material', width: 160, render: (_, row) => materials.find((material) => material.id === row.material_id)?.code ?? row.standard_parse?.material ?? (row.material_candidates.map((item) => item.value).join(' / ') || '—') },
          ...(!isAuto ? [{ title: '项目编号', key: 'project', width: 220, render: (_: unknown, row: RemnantImportItem) => row.project_no ?? row.project_candidates[0]?.value ?? '—' }] : []),
          { title: '项目编号二', dataIndex: 'project_no_secondary', width: 160, ellipsis: true, render: (value: string | null) => value || '—' },
          { title: '库存位置', dataIndex: 'storage_location', width: 150, ellipsis: true, render: (value: string | null) => value || '—' },
          { title: isAuto ? '零件编号' : '零件数', key: 'parts', width: isAuto ? 160 : 90, render: (_, row) => isAuto ? (row.parts.join('、') || row.standard_parse?.remnant_number || '—') : (row.parts.length || row.part_candidates.length) },
          ...(isAuto ? [
            { title: '原始规格', key: 'rawSpecification', width: 210, render: (_: unknown, row: RemnantImportItem) => row.standard_parse?.raw_specification ?? '—' },
            { title: '长 × 宽', key: 'dimensions', width: 150, render: (_: unknown, row: RemnantImportItem) => row.standard_parse ? `${row.standard_parse.length} × ${row.standard_parse.width}` : '—' },
            { title: '状态', dataIndex: 'status', width: 100, render: (status: RemnantImportItem['status']) => ({
              uploaded: '已上传', converting: '转换中', parsing: '解析中', pending_confirmation: '待确认',
              confirmed: '已导入', failed: '失败', cancelled: '已移除',
            })[status] },
          ] : []),
          { title: '校验结果', key: 'validation', width: 190, render: (_, row) => validationErrors[row.id] ? <Typography.Text type="danger">{describeRemnantCode(validationErrors[row.id])}</Typography.Text> : (row.status === 'confirmed' ? <Tag color="success">已确认</Tag> : '—') },
          { title: '操作', key: 'actions', width: isAuto ? 290 : 220, fixed: 'right', render: (_, row) => <Space size={2}>
            <Button type="link" icon={<EyeOutlined />} disabled={!row.dxf_file_id} onClick={() => setPreview(row)}>预览</Button>
            <Button type="link" icon={<EditOutlined />} disabled={row.status !== 'pending_confirmation'} onClick={() => edit(row)}>编辑</Button>
            {isAuto && row.status === 'failed' && <Button type="link" icon={<ReloadOutlined />} loading={itemAction.isPending && itemAction.variables?.itemId === row.id} onClick={() => itemAction.mutate({ action: 'retry', itemId: row.id })}>重试</Button>}
            {isAuto && !['confirmed', 'cancelled'].includes(row.status) && <Button danger type="link" icon={<CloseCircleOutlined />} loading={itemAction.isPending && itemAction.variables?.itemId === row.id} onClick={() => itemAction.mutate({ action: 'cancel', itemId: row.id })}>移除</Button>}
          </Space> },
        ]}
      />
      <Modal title="批量填写厚度" open={bulkOpen} onCancel={() => setBulkOpen(false)} onOk={() => bulk.mutate()} okButtonProps={{ disabled: !bulkThickness }} confirmLoading={bulk.isPending}>
        <InputNumber aria-label="批量厚度" min={0.001} precision={3} value={bulkThickness} onChange={(value) => setBulkThickness(value ?? undefined)} addonAfter="mm" style={{ width: '100%' }} />
      </Modal>
      <Modal title="批量设置项目编号" open={bulkProjectOpen} onCancel={() => setBulkProjectOpen(false)} onOk={() => applyProject.mutate()} okButtonProps={{ disabled: !bulkProject.trim() }} confirmLoading={applyProject.isPending}>
        <Input aria-label="批量项目编号" value={bulkProject} maxLength={128} onChange={(event) => setBulkProject(event.target.value)} placeholder="请输入项目编号" />
      </Modal>
      <Modal title="批量填写附加信息" open={bulkMetadataOpen} onCancel={() => setBulkMetadataOpen(false)} onOk={() => applyMetadata.mutate()} confirmLoading={applyMetadata.isPending}>
        <Typography.Paragraph type="secondary">两个字段均可留空；留空提交会清除所选图纸对应字段。</Typography.Paragraph>
        <Input aria-label="批量项目编号二" value={bulkProjectSecondary} maxLength={128} onChange={(event) => setBulkProjectSecondary(event.target.value)} placeholder="项目编号二（可空）" style={{ marginBottom: 12 }} />
        <Input aria-label="批量库存位置" value={bulkStorageLocation} maxLength={128} onChange={(event) => setBulkStorageLocation(event.target.value)} placeholder="库存位置（可空）" />
      </Modal>
      <Modal title={editing ? `确认 ${editing.original_name}` : '确认图纸'} open={Boolean(editing)} width={760} onCancel={closeEditor} onOk={() => form.submit()} confirmLoading={save.isPending}>
        {editing && <div className="remnant-confirm-editor">
          <div>
            <Button icon={<EyeOutlined />} disabled={!editing.dxf_file_id} onClick={() => setPreview(editing)}>打开图形预览</Button>
            {editing.warnings.map((warning) => <Alert key={warning.code} type="warning" showIcon title={warningTitle(warning.code)} description={warning.message} style={{ marginTop: 12 }} />)}
            <Descriptions size="small" column={1} style={{ marginTop: 16 }} items={[
              { key: 'material', label: '材质证据', children: editing.material_candidates.flatMap((item) => item.evidence).map((item) => `${item.layer}: ${item.raw_text}`).join('；') || '无' },
              { key: 'project', label: '项目证据', children: editing.project_candidates.flatMap((item) => item.evidence).map((item) => item.raw_text).join('；') || '无' },
            ]} />
          </div>
          <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
            <Form.Item name="thickness_mm" label="厚度（mm）" rules={[{ required: true, message: '请填写余料厚度' }]}><InputNumber min={0.001} precision={3} style={{ width: '100%' }} /></Form.Item>
            {(!selectedMaterialId || unmatchedMaterialCodes.length > 0) && <Alert
              type="info"
              showIcon
              title={unmatchedMaterialCodes.length > 0 ? `检测到未建档材质 ${unmatchedMaterialCodes.join(' / ')}` : '未检测到已建档材质，请填写完整牌号'}
              description={<Space.Compact style={{ width: '100%', marginTop: 8 }}>
                <AutoComplete
                  aria-label="新材质完整牌号"
                  value={materialCode}
                  options={unmatchedMaterialCodes.map((code) => ({ value: code }))}
                  onChange={(value) => {
                    materialCodeRef.current = value;
                    setMaterialCode(value);
                  }}
                  placeholder="选择候选或输入完整牌号"
                  style={{ flex: 1 }}
                />
                <Button
                  loading={createDetectedMaterial.isPending}
                  disabled={!normalizeMaterialCode(materialCode)}
                  onClick={() => {
                    const code = normalizeMaterialCode(materialCode);
                    materialCodeRef.current = code;
                    setMaterialCode(code);
                    createDetectedMaterial.mutate({ itemId: editing.id, code, generation: editorGenerationRef.current });
                  }}
                >{materialCode ? `新建并使用 ${normalizeMaterialCode(materialCode)}` : '新建并使用材质'}</Button>
              </Space.Compact>}
              style={{ marginBottom: 16 }}
            />}
            <Form.Item name="material_id" label="标准材质" rules={[{ required: true, message: '请选择或新建材质' }]}><Select showSearch optionFilterProp="label" options={materials.map((item) => ({ value: item.id, label: item.code }))} /></Form.Item>
            <Form.Item name="project_no" label="项目编号" rules={[{ required: true, message: '请填写项目编号' }]}><AutoComplete
              options={editing.project_candidates.map((candidate) => ({ value: candidate.value }))}
              allowClear
              placeholder="选择识别候选或手动填写"
            /></Form.Item>
            <Form.Item name="project_no_secondary" label="项目编号二（可空）"><Input allowClear /></Form.Item>
            <Form.Item name="storage_location" label="库存位置（可空）"><Input allowClear /></Form.Item>
            <Form.Item name="remark_1" label="备注一（可空）"><Input.TextArea rows={2} allowClear /></Form.Item>
            <Form.Item name="remark_2" label="备注二（可空）"><Input.TextArea rows={2} allowClear /></Form.Item>
            <Form.Item name="parts" label="零件编号" rules={[{ required: true, type: 'array', min: 1, message: '至少保留一个零件编号' }]}><Select
              mode="tags"
              tokenSeparators={['、', ',', '，', '\n']}
              options={editing.part_candidates.map((candidate) => ({ value: candidate.value, label: candidate.value }))}
              placeholder="默认全选识别结果，可取消或手动补充"
            /></Form.Item>
          </Form>
        </div>}
      </Modal>
      <DxfPreviewModal fileId={preview?.dxf_file_id ?? null} fileName={preview?.original_name ?? ''} open={Boolean(preview)} onClose={() => setPreview(undefined)} />
    </Card>
  );
}
