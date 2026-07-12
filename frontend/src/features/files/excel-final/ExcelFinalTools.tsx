import { useMemo, useState } from 'react';
import axios from 'axios';
import { Alert, Button, Card, Empty, Input, Space, Table, Tag, Typography } from 'antd';
import { ClearOutlined, SearchOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';

import {
  lookupExcelFinalWeight,
  searchExcelFinalParts,
  type ExcelFinalPartFilters,
} from '../../../api/excel-final.api';
import type { ExcelFinalPart } from '../../../types/excel-final';

type SearchFilters = Pick<ExcelFinalPartFilters, 'part_no' | 'spec' | 'material'>;

function apiError(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const body = error.response?.data as { error?: { message?: string } } | undefined;
    if (body?.error?.message) return body.error.message;
  }
  return error instanceof Error ? error.message : fallback;
}

export function ExcelFinalTools() {
  const [draft, setDraft] = useState<SearchFilters>({});
  const [applied, setApplied] = useState<SearchFilters | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [weightSpec, setWeightSpec] = useState('');

  const searchQ = useQuery({
    queryKey: ['excel-final-search', applied, page, pageSize],
    queryFn: () => searchExcelFinalParts(applied ?? {}, page, pageSize),
    enabled: applied !== null,
  });
  const weightQ = useMutation({ mutationFn: lookupExcelFinalWeight });

  const columns = useMemo(() => [
    { title: '零件号', dataIndex: 'part_no', width: 150, render: (value: string | null) => value || '-' },
    { title: '构件号', dataIndex: 'component_no', width: 150, render: (value: string | null) => value || '-' },
    { title: '规格', dataIndex: 'spec', width: 160, render: (value: string | null) => value ? <Tag color="blue">{value}</Tag> : '-' },
    { title: '材质', dataIndex: 'material', width: 100, render: (value: string | null) => value || '-' },
    { title: '数量', dataIndex: 'qty', width: 80 },
    { title: '净重 / kg', dataIndex: 'net_total_weight', width: 120, render: (value: number | null) => value?.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) ?? '-' },
    { title: '批次', dataIndex: 'batch_id', width: 90, render: (value: number) => `#${value}` },
  ], []);

  function applySearch() {
    const cleaned = Object.fromEntries(
      Object.entries(draft).map(([key, value]) => [key, value?.trim()]).filter(([, value]) => value),
    ) as SearchFilters;
    setPage(1);
    setApplied(cleaned);
  }

  function clearSearch() {
    setDraft({});
    setApplied(null);
    setPage(1);
  }

  return (
    <section className="excel-final-tools" aria-label="数据检索工具">
      <Card className="excel-final-tool-card" title={<><SearchOutlined /> 跨批次零件检索</>}>
        <Space wrap size={8} className="excel-final-search-controls">
          <Input aria-label="跨批次零件号" name="excel_final_part_no" autoComplete="off" placeholder="零件号…" allowClear value={draft.part_no ?? ''}
            onChange={(event) => setDraft((current) => ({ ...current, part_no: event.target.value }))}
            onPressEnter={applySearch} />
          <Input aria-label="跨批次规格" name="excel_final_spec" autoComplete="off" placeholder="规格…" allowClear value={draft.spec ?? ''}
            onChange={(event) => setDraft((current) => ({ ...current, spec: event.target.value }))}
            onPressEnter={applySearch} />
          <Input aria-label="跨批次材质" name="excel_final_material" autoComplete="off" placeholder="材质…" allowClear value={draft.material ?? ''}
            onChange={(event) => setDraft((current) => ({ ...current, material: event.target.value }))}
            onPressEnter={applySearch} />
          <Button type="primary" aria-label="搜索零件" icon={<SearchOutlined />} onClick={applySearch}>搜索</Button>
          <Button aria-label="清空搜索" icon={<ClearOutlined />} onClick={clearSearch}>清空</Button>
        </Space>
        {searchQ.isError && <Alert type="error" showIcon message={apiError(searchQ.error, '零件检索失败')} />}
        {applied === null ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="输入条件后检索 MySQL 中的跨批次零件记录" />
        ) : (
          <Table<ExcelFinalPart>
            rowKey="id"
            size="small"
            loading={searchQ.isLoading}
            dataSource={searchQ.data?.data ?? []}
            columns={columns}
            scroll={{ x: 900 }}
            pagination={{
              current: page,
              pageSize,
              total: searchQ.data?.pagination.total ?? 0,
              showSizeChanger: true,
            }}
            onChange={(pagination) => {
              setPage(pagination.current ?? 1);
              setPageSize(pagination.pageSize ?? 20);
            }}
          />
        )}
      </Card>

      <Card className="excel-final-tool-card is-weight" title={<><ThunderboltOutlined /> 五金手册理论重量</>}>
        <Typography.Paragraph type="secondary">
          查询型钢或板材的理论米重，结果来自独立五金手册数据库。
        </Typography.Paragraph>
        <Space.Compact block>
          <Input aria-label="钢材规格" name="excel_final_weight_spec" autoComplete="off" placeholder="例如 L50x5、PL10*200…" value={weightSpec}
            onChange={(event) => setWeightSpec(event.target.value)}
            onPressEnter={() => weightSpec.trim() && weightQ.mutate(weightSpec.trim())} />
          <Button type="primary" aria-label="查询理论重量" loading={weightQ.isPending}
            onClick={() => weightSpec.trim() && weightQ.mutate(weightSpec.trim())}>查询</Button>
        </Space.Compact>
        {weightQ.isError && <Alert type="error" showIcon message="比重查询失败" description={apiError(weightQ.error, '五金手册暂不可用')} />}
        {weightQ.data && (
          <div className="excel-final-weight-result">
            <span>{weightQ.data.spec}</span>
            <strong>{weightQ.data.weight_kg_per_m.toLocaleString('zh-CN')} kg/m</strong>
            <small>{weightQ.data.source}</small>
          </div>
        )}
      </Card>
    </section>
  );
}
