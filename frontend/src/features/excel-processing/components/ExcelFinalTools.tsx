import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  ClearOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';

import {
  lookupExcelFinalWeight,
  searchExcelFinalParts,
  type ExcelFinalPartFilters,
} from '../api';
import { describeApiError } from '../../../shared/api';
import type {
  ExcelFinalPart,
  HandbookCategory,
} from '../types';
import {
  DEFAULT_SEARCH_PAGE_SIZE,
  mergeExcelFinalParams,
  omitDefault,
  parseExcelFinalUrlState,
} from '../model/excelFinalUrlState';

type SearchFilters = Pick<ExcelFinalPartFilters, 'part_no' | 'spec' | 'material'>;

const CATEGORY_OPTIONS: Array<{ value: HandbookCategory; label: string }> = [
  { value: 'flat_steel', label: '扁钢' },
  { value: 'round_bar', label: '圆钢' },
  { value: 'rebar', label: '螺纹钢' },
  { value: 'square_bar', label: '方钢' },
  { value: 'i_beam', label: '工字钢' },
  { value: 'h_beam', label: 'H 型钢' },
  { value: 't_beam', label: 'T 型钢' },
  { value: 'channel', label: '槽钢' },
  { value: 'angle', label: '角钢' },
  { value: 'steel_pipe', label: '钢管（PIP）' },
  { value: 'square_tube', label: '方矩管（PD）' },
  { value: 'hfw_pipe', label: '高频焊管' },
  { value: 'w_beam', label: 'W 型钢' },
  { value: 'plate', label: '板材' },
  { value: 'skip', label: '螺栓、螺套、TT（留空）' },
];

function handbookValidation(
  category: HandbookCategory | undefined,
  spec: string,
  material: string,
): string | null {
  // 与后端 Handbook Material Routing 映射保持一致（有跨 seam 测试约束）：
  // D 系列（D8 这类直径规格）必须带材质，且 HRB→螺纹钢、HPB/Q235B/Q355B
  // →圆钢互斥；修改任一侧都必须同步（Stage/后端适配器/前端三侧）。
  if (!category) return '请选择五金手册类别。';
  if (!spec.trim()) return '请输入完整规格。';
  const normalizedMaterial = material.replace(/[ 　]/g, '').toUpperCase();
  if (['round_bar', 'rebar'].includes(category) && !normalizedMaterial) {
    return '圆钢和螺纹钢查询必须填写材质。';
  }
  if (/^D\d+(?:\.\d+)?$/i.test(spec.trim())) {
    const expected = normalizedMaterial.startsWith('HRB')
      ? 'rebar'
      : ['HPB', 'Q235B', 'Q355B'].some((prefix) => normalizedMaterial.startsWith(prefix))
        ? 'round_bar'
        : null;
    if (!expected) {
      return 'D 系列材质仅支持 HRB、HPB、Q235B、Q355B；请核对材质。';
    }
    if (category !== expected) {
      return expected === 'rebar'
        ? 'HRB 材质的 D 系列必须选择“螺纹钢”。'
        : 'HPB、Q235B、Q355B 材质的 D 系列必须选择“圆钢”。';
    }
  }
  return null;
}

export function ExcelFinalTools({ mode }: { mode: 'parts' | 'handbook' }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlState = useMemo(() => parseExcelFinalUrlState(searchParams), [searchParams]);
  const [draft, setDraft] = useState<SearchFilters>({
    part_no: urlState.partNo,
    spec: urlState.spec,
    material: urlState.material,
  });
  const [category, setCategory] = useState<HandbookCategory>();
  const [weightSpec, setWeightSpec] = useState('');
  const [weightMaterial, setWeightMaterial] = useState('');
  const [handbookError, setHandbookError] = useState<string | null>(null);
  const applied: SearchFilters | null = urlState.searchApplied
    ? { part_no: urlState.partNo, spec: urlState.spec, material: urlState.material }
    : null;
  const page = urlState.searchPage;
  const pageSize = urlState.searchPageSize;

  useEffect(() => {
    setDraft({
      part_no: urlState.partNo,
      spec: urlState.spec,
      material: urlState.material,
    });
  }, [urlState.partNo, urlState.spec, urlState.material]);

  const searchQ = useQuery({
    queryKey: ['excel-final-search', applied, page, pageSize],
    queryFn: () => searchExcelFinalParts(applied ?? {}, page, pageSize),
    enabled: mode === 'parts' && applied !== null,
  });
  const weightQ = useMutation({ mutationFn: lookupExcelFinalWeight });

  const columns = useMemo(() => [
    { title: '零件号', dataIndex: 'part_no', width: 150, render: (value: string | null) => value || '-' },
    { title: '构件号', dataIndex: 'component_no', width: 150, render: (value: string | null) => value || '-' },
    { title: '规格', dataIndex: 'spec', width: 180, render: (value: string | null) => value ? <Tag color="blue">{value}</Tag> : '-' },
    { title: '材质', dataIndex: 'material', width: 110, render: (value: string | null) => value || '-' },
    { title: '数量', dataIndex: 'qty', width: 80 },
    { title: '净重 / kg', dataIndex: 'net_total_weight', width: 120, render: (value: number | null) => value?.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) ?? '-' },
    { title: '批次', dataIndex: 'batch_id', width: 90, render: (value: number) => `#${value}` },
  ], []);

  function applySearch() {
    const cleaned = Object.fromEntries(
      Object.entries(draft)
        .map(([key, value]) => [key, value?.trim()])
        .filter(([, value]) => value),
    ) as SearchFilters;
    setSearchParams(mergeExcelFinalParams(searchParams, {
      tab: 'parts',
      search: '1',
      part_no: cleaned.part_no,
      spec: cleaned.spec,
      material: cleaned.material,
      search_page: null,
    }));
  }

  function clearSearch() {
    setDraft({});
    setSearchParams(mergeExcelFinalParams(searchParams, {
      tab: 'parts',
      search: null,
      part_no: null,
      spec: null,
      material: null,
      search_page: null,
      search_size: null,
    }));
  }

  function lookupWeight() {
    const validation = handbookValidation(category, weightSpec, weightMaterial);
    setHandbookError(validation);
    if (validation || !category) return;
    weightQ.reset();
    const material = weightMaterial.replace(/[ 　]/g, '').toUpperCase();
    weightQ.mutate({
      category,
      spec: weightSpec.trim(),
      ...(material ? { material } : {}),
    });
  }

  if (mode === 'parts') {
    return (
      <section className="excel-final-tools is-single" aria-label="跨批次零件检索">
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
          {searchQ.isError && <Alert type="error" showIcon message={describeApiError(searchQ.error, '零件检索失败')} />}
          {applied === null ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="输入条件后检索已完成处理的跨批次零件记录" />
          ) : (
            <Table<ExcelFinalPart>
              rowKey="id"
              size="small"
              loading={searchQ.isLoading}
              dataSource={searchQ.data?.data ?? []}
              columns={columns}
              scroll={{ x: 920 }}
              pagination={{
                current: page,
                pageSize,
                total: searchQ.data?.pagination.total ?? 0,
                showSizeChanger: true,
              }}
              onChange={(pagination) => {
                const nextSize = pagination.pageSize ?? DEFAULT_SEARCH_PAGE_SIZE;
                const sizeChanged = nextSize !== pageSize;
                setSearchParams(mergeExcelFinalParams(searchParams, {
                  tab: 'parts',
                  search: '1',
                  search_page: omitDefault(sizeChanged ? 1 : (pagination.current ?? 1), 1),
                  search_size: omitDefault(nextSize, DEFAULT_SEARCH_PAGE_SIZE),
                }));
              }}
            />
          )}
        </Card>
      </section>
    );
  }

  const lookup = weightQ.data;
  return (
    <section className="excel-final-handbook" aria-label="五金手册查询">
      <Card className="excel-final-tool-card is-weight" title={<><ThunderboltOutlined /> 权威五金手册查询</>}>
        <div className="excel-final-handbook-grid">
          <div className="excel-final-handbook-form">
            <Typography.Paragraph type="secondary">
              按类别、完整规格和必要材质查询唯一金标准。类别不是模糊筛选，而是手册表的物理分类。
            </Typography.Paragraph>
            <label className="excel-final-field">
              <span>类别</span>
              <Select
                aria-label="五金手册类别"
                value={category}
                placeholder="先选择物理类别"
                options={CATEGORY_OPTIONS}
                onChange={(value) => {
                  setCategory(value);
                  setHandbookError(null);
                  weightQ.reset();
                }}
              />
            </label>
            <label className="excel-final-field">
              <span>完整规格</span>
              <Input
                aria-label="钢材规格"
                name="excel_final_weight_spec"
                autoComplete="off"
                placeholder="例如 6*30、D8、L50*5"
                value={weightSpec}
                onChange={(event) => {
                  setWeightSpec(event.target.value);
                  setHandbookError(null);
                }}
                onPressEnter={lookupWeight}
              />
            </label>
            <label className="excel-final-field">
              <span>材质</span>
              <Input
                aria-label="钢材材质"
                name="excel_final_weight_material"
                autoComplete="off"
                placeholder="D 系列必填，例如 Q235B、HRB400"
                value={weightMaterial}
                onChange={(event) => {
                  setWeightMaterial(event.target.value);
                  setHandbookError(null);
                }}
                onPressEnter={lookupWeight}
              />
            </label>
            <Button
              type="primary"
              size="large"
              aria-label="查询理论重量"
              loading={weightQ.isPending}
              onClick={lookupWeight}
            >
              查询理论重量
            </Button>
            {handbookError && <Alert type="warning" showIcon message={handbookError} />}
            {weightQ.isError && (
              <Alert
                type="error"
                showIcon
                message="五金手册查询失败"
                description={describeApiError(weightQ.error, '请核对类别、规格和材质')}
              />
            )}
          </div>
          <div className="excel-final-handbook-rules">
            <strong>查询规则</strong>
            <ol>
              <li>板材按厚度和尺寸公式计算，钢材比重统一取 7.85。</li>
              <li>扁钢及其他型钢按对应物理类别查权威手册，不跨类别择值。</li>
              <li>D 系列同时看材质：HPB、Q235B、Q355B 查圆钢；HRB 查螺纹钢。</li>
              <li>PIP、PD 按截面尺寸公式计算；螺栓、螺套和 TT 留空。</li>
              <li>权威源没有记录则标记“查无”；同键不同重量则标记“冲突”，都不得猜值。</li>
            </ol>
          </div>
        </div>
        {lookup?.status === 'hit' && lookup.weight_kg_per_m !== null && (
          <div className="excel-final-weight-result">
            <span>{lookup.category} · {lookup.normalized_spec}{lookup.material ? ` · ${lookup.material}` : ''}</span>
            <strong>{lookup.weight_kg_per_m.toLocaleString('zh-CN')} kg/m</strong>
            <small>来源：{lookup.source}</small>
          </div>
        )}
        {lookup?.status === 'not_found' && (
          <Alert
            type="error"
            showIcon
            message="权威五金手册查无"
            description="该类别、规格和材质组合没有可信记录；正式结果应红色标记“查无”，交由人工处理。"
          />
        )}
        {lookup?.status === 'conflict' && (
          <Alert
            type="error"
            showIcon
            message="权威源存在重量冲突"
            description="相同查询键对应多个不同重量，系统不会擅自择一；请人工核对源手册。"
          />
        )}
        {lookup?.status === 'skipped' && (
          <Alert
            type="info"
            showIcon
            message="按业务规则留空"
            description="螺栓、螺套和 TT 不查询理论重量，结果保持空值。"
          />
        )}
      </Card>
    </section>
  );
}
