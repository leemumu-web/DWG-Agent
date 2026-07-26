import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Modal,
  Space,
  Table,
  Tabs,
  Tag,
} from 'antd';
import { EyeOutlined, FilterOutlined, ReloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';

import {
  getExcelFinalBatch,
  getExcelFinalPart,
  listExcelFinalComponents,
  listExcelFinalParts,
  type ExcelFinalPartFilters,
} from '../api';
import { describeApiError } from '../../../shared/api';
import type { ExcelFinalComponent, ExcelFinalPart } from '../types';

interface ExcelFinalBatchDrawerProps {
  batchId: number | null;
  open: boolean;
  onClose: () => void;
}

function number(value: number | null | undefined, digits = 2): string {
  return value == null ? '-' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

export function ExcelFinalBatchDrawer({ batchId, open, onClose }: ExcelFinalBatchDrawerProps) {
  const [tab, setTab] = useState('parts');
  const [partPage, setPartPage] = useState(1);
  const [partPageSize, setPartPageSize] = useState(50);
  const [componentPage, setComponentPage] = useState(1);
  const [componentPageSize, setComponentPageSize] = useState(20);
  const [filterDraft, setFilterDraft] = useState<ExcelFinalPartFilters>({});
  const [filters, setFilters] = useState<ExcelFinalPartFilters>({});
  const [partId, setPartId] = useState<number | null>(null);

  useEffect(() => {
    if (!open) return;
    setTab('parts');
    setPartPage(1);
    setComponentPage(1);
    setFilterDraft({});
    setFilters({});
    setPartId(null);
  }, [batchId, open]);

  const detailQ = useQuery({
    queryKey: ['excel-final-batch', batchId],
    queryFn: () => getExcelFinalBatch(batchId!),
    enabled: open && batchId !== null,
  });
  const partsQ = useQuery({
    queryKey: ['excel-final-parts', batchId, partPage, partPageSize, filters],
    queryFn: () => listExcelFinalParts(batchId!, partPage, partPageSize, filters),
    enabled: open && batchId !== null && tab === 'parts',
  });
  const componentsQ = useQuery({
    queryKey: ['excel-final-components', batchId, componentPage, componentPageSize],
    queryFn: () => listExcelFinalComponents(batchId!, componentPage, componentPageSize),
    enabled: open && batchId !== null && tab === 'components',
  });
  const partQ = useQuery({
    queryKey: ['excel-final-part', batchId, partId],
    queryFn: () => getExcelFinalPart(batchId!, partId!),
    enabled: open && batchId !== null && partId !== null,
  });

  const partColumns = useMemo(() => [
    { title: '序号', dataIndex: 'seq', width: 70 },
    { title: '构件号', dataIndex: 'component_no', width: 160, ellipsis: true },
    { title: '零件号', dataIndex: 'part_no', width: 160, ellipsis: true },
    { title: '类型', dataIndex: 'part_type', width: 100 },
    { title: '规格', dataIndex: 'spec', width: 150, ellipsis: true },
    { title: '材质', dataIndex: 'material', width: 90 },
    { title: '数量', dataIndex: 'qty', width: 80, render: (value: number | null) => number(value) },
    { title: '净重 / kg', dataIndex: 'net_total_weight', width: 120, render: (value: number | null) => number(value) },
    {
      title: '',
      key: 'action',
      width: 54,
      fixed: 'right' as const,
      render: (_: unknown, record: ExcelFinalPart) => (
        <Button
          type="text"
          icon={<EyeOutlined />}
          aria-label={`查看零件 ${record.part_no || record.id}`}
          onClick={() => setPartId(record.id)}
        />
      ),
    },
  ], []);
  const componentColumns = useMemo(() => [
    { title: '构件号', dataIndex: 'component_no', ellipsis: true },
    { title: '构件数量', dataIndex: 'component_qty', width: 120, render: (value: number | null) => number(value, 0) },
    { title: '总重 / kg', dataIndex: 'total_weight', width: 150, render: (value: number | null) => number(value) },
  ], []);

  function applyFilters() {
    setPartPage(1);
    setFilters(Object.fromEntries(
      Object.entries(filterDraft).map(([key, value]) => [key, value?.trim()]).filter(([, value]) => value),
    ) as ExcelFinalPartFilters);
  }

  const detail = detailQ.data;
  return (
    <>
      <Drawer
        className="excel-final-batch-drawer"
        title={`批次 #${batchId ?? ''} · 数据明细`}
        open={open}
        size="min(1180px, 96vw)"
        onClose={onClose}
        extra={<Button icon={<ReloadOutlined />} onClick={() => void detailQ.refetch()}>刷新</Button>}
      >
        {detailQ.isError && <Alert type="error" showIcon message={describeApiError(detailQ.error, '批次详情加载失败')} />}
        {detail && (
          <>
            <div className="excel-final-batch-summary">
              <div>
                <span>SOURCE FILE</span>
                <strong>{detail.source_name ?? '未命名来源'}</strong>
                <small>{detail.source_type} · Job #{detail.job_id}</small>
              </div>
              <div><strong>{number(detail.part_count, 0)}</strong><span>零件</span></div>
              <div><strong>{number(detail.component_count, 0)}</strong><span>构件</span></div>
              <div><strong>{number(detail.total_net_weight)}</strong><span>净重 kg</span></div>
            </div>
            <div className="excel-final-breakdowns">
              <Space wrap size={[5, 5]}>
                {detail.material_breakdown.map((item) => (
                  <Tag key={item.material} color="geekblue">{item.material} · {item.count}</Tag>
                ))}
              </Space>
              <Space wrap size={[5, 5]}>
                {detail.top_specs.slice(0, 8).map((item) => (
                  <Tag key={item.spec}>{item.spec} · {item.count}</Tag>
                ))}
              </Space>
            </div>
          </>
        )}

        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={[
            {
              key: 'parts',
              label: `零件 ${detail?.part_count ?? ''}`,
              children: (
                <>
                  <Space wrap size={8} className="excel-final-drawer-filters">
                    <Input placeholder="零件号…" aria-label="批次零件号" name="batch_part_no" autoComplete="off" allowClear value={filterDraft.part_no ?? ''}
                      onChange={(event) => setFilterDraft((current) => ({ ...current, part_no: event.target.value }))}
                      onPressEnter={applyFilters} />
                    <Input placeholder="规格…" aria-label="批次规格" name="batch_spec" autoComplete="off" allowClear value={filterDraft.spec ?? ''}
                      onChange={(event) => setFilterDraft((current) => ({ ...current, spec: event.target.value }))}
                      onPressEnter={applyFilters} />
                    <Input placeholder="材质…" aria-label="批次材质" name="batch_material" autoComplete="off" allowClear value={filterDraft.material ?? ''}
                      onChange={(event) => setFilterDraft((current) => ({ ...current, material: event.target.value }))}
                      onPressEnter={applyFilters} />
                    <Button icon={<FilterOutlined />} type="primary" onClick={applyFilters}>筛选</Button>
                    <Button onClick={() => { setFilterDraft({}); setFilters({}); setPartPage(1); }}>重置</Button>
                  </Space>
                  {partsQ.isError && <Alert type="error" showIcon message={describeApiError(partsQ.error, '零件列表加载失败')} />}
                  <Table<ExcelFinalPart>
                    rowKey="id"
                    size="small"
                    loading={partsQ.isLoading}
                    dataSource={partsQ.data?.data ?? []}
                    columns={partColumns}
                    scroll={{ x: 1100 }}
                    pagination={{ current: partPage, pageSize: partPageSize, total: partsQ.data?.pagination.total ?? 0, showSizeChanger: true }}
                    onChange={(pagination) => { setPartPage(pagination.current ?? 1); setPartPageSize(pagination.pageSize ?? 50); }}
                  />
                </>
              ),
            },
            {
              key: 'components',
              label: `构件 ${detail?.component_count ?? ''}`,
              children: componentsQ.isError ? (
                <Alert type="error" showIcon message={describeApiError(componentsQ.error, '构件列表加载失败')} />
              ) : (
                <Table<ExcelFinalComponent>
                  rowKey="id"
                  size="small"
                  loading={componentsQ.isLoading}
                  dataSource={componentsQ.data?.data ?? []}
                  columns={componentColumns}
                  pagination={{ current: componentPage, pageSize: componentPageSize, total: componentsQ.data?.pagination.total ?? 0, showSizeChanger: true }}
                  onChange={(pagination) => { setComponentPage(pagination.current ?? 1); setComponentPageSize(pagination.pageSize ?? 20); }}
                />
              ),
            },
          ]}
        />
        {!detailQ.isLoading && !detail && !detailQ.isError && <Empty description="批次不存在" />}
      </Drawer>

      <Modal
        title={`零件 ${partQ.data?.part_no ?? partId ?? ''} · 数据库记录`}
        open={partId !== null}
        onCancel={() => setPartId(null)}
        footer={<Button onClick={() => setPartId(null)}>关闭</Button>}
        width={760}
        destroyOnHidden
      >
        {partQ.isError && <Alert type="error" showIcon message={describeApiError(partQ.error, '零件详情加载失败')} />}
        {partQ.data && (
          <Descriptions
            bordered
            size="small"
            column={{ xs: 1, sm: 2 }}
            items={[
              { key: 'component', label: '构件号', children: partQ.data.component_no ?? '-' },
              { key: 'part', label: '零件号', children: partQ.data.part_no ?? '-' },
              { key: 'profile', label: '型材规格', children: partQ.data.profile_spec ?? '-' },
              { key: 'spec', label: '规格', children: partQ.data.spec ?? '-' },
              { key: 'material', label: '材质', children: partQ.data.material ?? '-' },
              { key: 'qty', label: '总数量', children: number(partQ.data.total_qty) },
              { key: 'length', label: '切割长度', children: number(partQ.data.cut_length) },
              { key: 'net', label: '净总重 / kg', children: number(partQ.data.net_total_weight) },
              { key: 'gross', label: '毛总重 / kg', children: number(partQ.data.gross_total_weight) },
              { key: 'surface', label: '总面积 / m²', children: number(partQ.data.total_surface_area, 3) },
            ]}
          />
        )}
      </Modal>
    </>
  );
}
