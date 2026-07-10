import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  App,
  Button,
  Card,
  Descriptions,
  Drawer,
  Input,
  message,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  Alert,
  Divider,
  Upload,
} from 'antd';
import {
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileTextOutlined,
  CheckCircleFilled,
  SyncOutlined,
  CloseCircleFilled,
  InboxOutlined,
  ReloadOutlined,
  SearchOutlined,
  TableOutlined,
  BarChartOutlined,
  PieChartOutlined,
  ThunderboltOutlined,
  InfoCircleOutlined,
  ScissorOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  uploadAndProcess,
  getProcessStatus,
  downloadProcessResult,
  listBatches,
  getBatchDetail,
  listBatchParts,
  listBatchComponents,
  searchParts,
  getPartDetail,
  lookupWeight,
  checkHealth,
} from '../../api/excel-final.api';
import ExcelPreview from '../../components/ExcelPreview';
import type { ExcelFinalBatch, ExcelFinalPart, ExcelFinalComponent, BatchDetail as BatchDetailType, ExcelFinalHealth } from '../../types/excel-final';
import { fmtDateTime } from '../../components/ui';
import type { PageEnvelope } from '../../api/client';

const { Text, Title } = Typography;
const { Dragger } = Upload;

const STATUS: Record<string, { color: string; bg: string; label: string; icon: React.ReactNode }> = {
  succeeded: { color: '#52c41a', bg: '#f6ffed', label: '已完成', icon: <CheckCircleFilled style={{ color: '#52c41a' }} /> },
  running:   { color: '#1677ff', bg: '#e6f4ff', label: '处理中', icon: <SyncOutlined style={{ color: '#1677ff' }} spin /> },
  queued:    { color: '#faad14', bg: '#fffbe6', label: '排队中', icon: <SyncOutlined style={{ color: '#faad14' }} /> },
  failed:    { color: '#ff4d4f', bg: '#fff2f0', label: '失败',   icon: <CloseCircleFilled style={{ color: '#ff4d4f' }} /> },
  cancelled: { color: '#8c8c8c', bg: '#fafafa', label: '已取消', icon: <CloseCircleFilled style={{ color: '#8c8c8c' }} /> },
};

// ── page ──────────────────────────────────────────────────────────────────

export function ExcelFinalPage() {
  const { message: msg } = App.useApp();
  const queryClient = useQueryClient();

  // Data
  const batchesQ = useQuery({
    queryKey: ['excel-final', 'batches'],
    queryFn: () => listBatches(1, 50),
    staleTime: 3000,
  });

  // Upload state
  const [uploading, setUploading] = useState(false);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [polling, setPolling] = useState(false);

  // Detail drawer
  const [detailBatch, setDetailBatch] = useState<BatchDetailType | null>(null);
  const [detailParts, setDetailParts] = useState<PageEnvelope<ExcelFinalPart> | null>(null);
  const [detailComponents, setDetailComponents] = useState<ExcelFinalComponent[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [partsPage, setPartsPage] = useState(1);
  const [partsFilters, setPartsFilters] = useState<Record<string, string>>({});

  // Excel preview modal
  const [previewFileId, setPreviewFileId] = useState<number | null>(null);
  const [previewFileName, setPreviewFileName] = useState('');
  const [activeJobResultFileId, setActiveJobResultFileId] = useState<number | null>(null);

  // Health check
  const healthQ = useQuery({
    queryKey: ['excel-final', 'health'],
    queryFn: checkHealth,
    staleTime: 30000,
  });
  const health = healthQ.data as ExcelFinalHealth | undefined;

  // Part detail modal
  const [partDetail, setPartDetail] = useState<ExcelFinalPart | null>(null);
  const [partDetailLoading, setPartDetailLoading] = useState(false);

  // Weight lookup tool
  const [weightSpec, setWeightSpec] = useState('');
  const [weightResult, setWeightResult] = useState<{ weight: number; source: string } | null>(null);
  const [weightLoading, setWeightLoading] = useState(false);

  const batches = (batchesQ.data?.data ?? []) as ExcelFinalBatch[];

  // Poll active job
  useEffect(() => {
    if (!activeJobId || !polling) return;
    const id = setInterval(async () => {
      try {
        const status = await getProcessStatus(activeJobId);
        if (status.status === 'succeeded' || status.status === 'failed' || status.status === 'cancelled') {
          setPolling(false);
          setUploading(false);
          if (status.status === 'succeeded') {
            msg.success(`处理完成！${status.batch ? `${status.batch.part_count} 个零件已入库` : ''}`);
            if (status.result_file_id) setActiveJobResultFileId(status.result_file_id);
            batchesQ.refetch();
          } else if (status.status === 'failed') {
            msg.error(`处理失败: ${status.error_message || status.error_code || '未知错误'}`);
          }
        }
        // Keep polling for running/queued
      } catch { /* ignore poll errors */ }
    }, 2000);
    return () => clearInterval(id);
  }, [activeJobId, polling, msg, batchesQ]);

  // Upload handler
  const handleUpload = useCallback(async (file: File) => {
    setUploading(true);
    setActiveJobResultFileId(null);
    try {
      const result = await uploadAndProcess(file);
      setActiveJobId(result.job_id);
      setPolling(true);
      msg.info(`已提交处理任务 #${result.job_id}，等待 worker 处理…`);
    } catch (err) {
      msg.error(err instanceof Error ? err.message : '上传失败');
      setUploading(false);
    }
    return false; // prevent default upload
  }, [msg]);

  // Detail drawer
  const openDetail = useCallback(async (batch: ExcelFinalBatch) => {
    setDetailLoading(true);
    setDetailBatch(null);
    setDetailParts(null);
    setDetailComponents([]);
    setPartsPage(1);
    setPartsFilters({});
    try {
      const [bd, parts, comps] = await Promise.all([
        getBatchDetail(batch.batch_id),
        listBatchParts(batch.batch_id),
        listBatchComponents(batch.batch_id),
      ]);
      setDetailBatch(bd);
      setDetailParts(parts);
      setDetailComponents(comps);
    } catch (err) {
      msg.error(err instanceof Error ? err.message : '加载失败');
    }
    setDetailLoading(false);
  }, [msg]);

  const loadPartsPage = useCallback(async (page: number, filters: Record<string, string>) => {
    if (!detailBatch) return;
    try {
      const result = await listBatchParts(detailBatch.batch_id, filters, page);
      setDetailParts(result);
    } catch { /* ignore */ }
  }, [detailBatch]);

  // Download Excel result
  const handleDownloadResult = useCallback(async (jobId: number, fileName: string) => {
    try {
      const { url } = await downloadProcessResult(jobId);
      const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
      const a = document.createElement('a');
      a.href = `${baseUrl}${url}`;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { document.body.removeChild(a); }, 100);
    } catch (err) {
      msg.error(err instanceof Error ? err.message : '下载失败');
    }
  }, [msg]);

  // Part detail modal
  const openPartDetail = useCallback(async (batchId: number, partId: number) => {
    setPartDetailLoading(true);
    setPartDetail(null);
    try {
      const detail = await getPartDetail(batchId, partId);
      setPartDetail(detail);
    } catch (err) {
      msg.error(err instanceof Error ? err.message : '获取零件详情失败');
    }
    setPartDetailLoading(false);
  }, [msg]);

  // Weight lookup
  const handleWeightLookup = useCallback(async () => {
    if (!weightSpec.trim()) return;
    setWeightLoading(true);
    setWeightResult(null);
    try {
      const result = await lookupWeight(weightSpec.trim());
      setWeightResult({ weight: result.weight_kg_per_m, source: result.source });
    } catch (err) {
      msg.error(err instanceof Error ? err.message : '查询失败');
    }
    setWeightLoading(false);
  }, [weightSpec, msg]);

  // Cross-batch search
  const [searchFilters, setSearchFilters] = useState<Record<string, string>>({});
  const [searchPage, setSearchPage] = useState(1);
  const searchQ = useQuery({
    queryKey: ['excel-final', 'search', searchFilters, searchPage],
    queryFn: () => searchParts(
      { spec: searchFilters.spec, material: searchFilters.material, part_no: searchFilters.part_no },
      searchPage,
    ),
    enabled: !!(searchFilters.spec || searchFilters.material || searchFilters.part_no),
    staleTime: 5000,
  });

  // Job status tags in batch table
  const [batchJobStatuses, setBatchJobStatuses] = useState<Record<number, { status: string }>>({});
  useEffect(() => {
    if (batches.length === 0) return;
    // Poll job statuses for batches that might be running
    const check = async () => {
      const updates: Record<number, { status: string }> = {};
      for (const b of batches) {
        try {
          const st = await getProcessStatus(b.job_id);
          updates[b.batch_id] = { status: st.status };
        } catch { /* job may be gone */ }
      }
      setBatchJobStatuses((prev) => ({ ...prev, ...updates }));
    };
    check();
    const id = setInterval(check, 5000);
    return () => clearInterval(id);
  }, [batches]);

  // Stats
  const totalParts = batches.reduce((s, b) => s + b.part_count, 0);
  const totalComponents = batches.reduce((s, b) => s + b.component_count, 0);
  const totalNetWeight = batches.reduce((s, b) => s + (b.total_net_weight ?? 0), 0);

  // Error state
  if (batchesQ.isError && !batchesQ.data) {
    return (
      <Alert type="error" message="加载批次列表失败"
        description={(batchesQ.error as Error)?.message || '请检查后端服务或确认 EXCEL_FINAL_PIPELINE_ENABLED=true'}
        showIcon action={<Button size="small" onClick={() => batchesQ.refetch()}>重试</Button>} />
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {/* ── header ──────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>📊 Excel → 最终零件清单</Title>
          <Text type="secondary">上传从 DXF 提取的 Excel，自动整理为零件清单和构件表</Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => batchesQ.refetch()} loading={batchesQ.isFetching}>刷新</Button>
      </div>

      {/* ── health status ───────────────────────────────────────────────── */}
      {health && !health.ready && (
        <Alert
          type="warning"
          showIcon
          icon={<InfoCircleOutlined />}
          message="excel_final 管道未就绪"
          description={
            health.pipeline_enabled
              ? 'excel_final Python 包不可用，请安装 Stages/excel_final 包。'
              : '请在 .env.docker 中设置 EXCEL_FINAL_PIPELINE_ENABLED=true 以启用水线。'
          }
          style={{ marginBottom: 16 }}
        />
      )}

      {/* ── stats ───────────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        {[
          { label: '处理批次', value: batches.length, icon: <TableOutlined />, color: '#1677ff', bg: '#e6f4ff' },
          { label: '零件总数', value: totalParts, icon: <PieChartOutlined />, color: '#52c41a', bg: '#f6ffed' },
          { label: '构件总数', value: totalComponents, icon: <BarChartOutlined />, color: '#722ed1', bg: '#f9f0ff' },
          { label: '净重合计 (kg)', value: totalNetWeight.toFixed(1), icon: <ThunderboltOutlined />, color: '#faad14', bg: '#fffbe6' },
        ].map((s) => (
          <div key={s.label} style={{ background: s.bg, borderRadius: 10, padding: '14px 18px',
            display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 22, color: s.color }}>{s.icon}</span>
            <div>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#1f1f1f', lineHeight: 1.2 }}>{s.value}</div>
              <div style={{ fontSize: 13, color: '#8c8c8c', marginTop: 2 }}>{s.label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* ── upload area ─────────────────────────────────────────────── */}
      {!uploading && (
        <Dragger
          accept=".xlsx,.xls"
          showUploadList={false}
          beforeUpload={handleUpload}
          style={{ borderRadius: 10, padding: '16px 0' }}
        >
          <p className="ant-upload-drag-icon">
            <CloudUploadOutlined style={{ fontSize: 40, color: '#1677ff' }} />
          </p>
          <p className="ant-upload-text">📤 上传 Excel 并自动处理</p>
          <p className="ant-upload-hint">
            支持 .xlsx / .xls 格式，最大 512 MB · 自动识别初始表/Tekla TSV 格式
          </p>
        </Dragger>
      )}

      {/* ── processing indicator ─────────────────────────────────────── */}
      {uploading && activeJobId && (
        <Card style={{ borderRadius: 10, background: '#e6f4ff' }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <SyncOutlined spin style={{ fontSize: 20, color: '#1677ff' }} />
              <Text strong style={{ fontSize: 15 }}>处理任务 #{activeJobId} 运行中…</Text>
              <Button size="small" onClick={() => { setPolling(false); setUploading(false); }}>后台运行</Button>
            </div>
            <Progress percent={99} status="active" strokeColor="#1677ff" showInfo={false} />
            {activeJobResultFileId && (
              <Space>
                <Button type="primary" size="small" icon={<EyeOutlined />}
                  onClick={() => { setPreviewFileId(activeJobResultFileId); setPreviewFileName('处理结果.xlsx'); }}>
                  预览结果
                </Button>
                <Button size="small" icon={<DownloadOutlined />}
                  onClick={() => handleDownloadResult(activeJobId, '处理结果.xlsx')}>
                  下载 Excel
                </Button>
              </Space>
            )}
          </Space>
        </Card>
      )}

      {/* ── search & tools bar ─────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Cross-batch search */}
        <Card size="small" title={<span><SearchOutlined /> 跨批次零件搜索</span>} style={{ borderRadius: 10 }}>
          <Space wrap>
            <Input placeholder="零件号" allowClear style={{ width: 130 }}
              value={searchFilters.part_no || ''}
              onChange={(e) => { setSearchFilters((f) => ({ ...f, part_no: e.target.value })); setSearchPage(1); }} />
            <Input placeholder="规格" allowClear style={{ width: 120 }}
              value={searchFilters.spec || ''}
              onChange={(e) => { setSearchFilters((f) => ({ ...f, spec: e.target.value })); setSearchPage(1); }} />
            <Input placeholder="材质" allowClear style={{ width: 100 }}
              value={searchFilters.material || ''}
              onChange={(e) => { setSearchFilters((f) => ({ ...f, material: e.target.value })); setSearchPage(1); }} />
            <Button onClick={() => { setSearchFilters({}); setSearchPage(1); }} size="small">清除</Button>
          </Space>
          {searchQ.data && searchQ.data.data && searchQ.data.data.length > 0 && (
            <Table
              dataSource={searchQ.data.data}
              rowKey="id"
              size="small"
              loading={searchQ.isFetching}
              pagination={{
                current: searchPage,
                pageSize: 50,
                total: searchQ.data.pagination?.total ?? 0,
                onChange: (p) => setSearchPage(p),
                showTotal: (t) => `共 ${t} 个结果`,
              }}
              style={{ marginTop: 8 }}
              columns={[
                { title: '批次', dataIndex: 'batch_id', width: 60 },
                { title: '零件号', dataIndex: 'part_no', width: 100, ellipsis: true },
                { title: '规格', dataIndex: 'spec', width: 100, ellipsis: true },
                { title: '材质', dataIndex: 'material', width: 80 },
                { title: '长', dataIndex: 'length', width: 70, align: 'right' as const, render: (v: number | null) => v ?? '—' },
                { title: '数量', dataIndex: 'qty', width: 60, align: 'right' as const },
                { title: '净重', dataIndex: 'net_total_weight', width: 80, align: 'right' as const, render: (v: number | null) => v != null ? v.toFixed(1) : '—' },
              ]}
            />
          )}
        </Card>

        {/* Weight lookup tool */}
        <Card size="small" title={<span><ToolOutlined /> 五金手册比重查询</span>} style={{ borderRadius: 10 }}>
          <Space>
            <Input placeholder="规格, e.g. L50x5" allowClear style={{ width: 200 }}
              value={weightSpec}
              onChange={(e) => setWeightSpec(e.target.value)}
              onPressEnter={handleWeightLookup} />
            <Button type="primary" icon={<SearchOutlined />} loading={weightLoading}
              onClick={handleWeightLookup}>
              查询
            </Button>
          </Space>
          {weightResult && (
            <div style={{ marginTop: 12, padding: '10px 16px', background: '#f6ffed', borderRadius: 8, border: '1px solid #b7eb8f' }}>
              <Text strong style={{ fontSize: 15 }}>{weightSpec}</Text>
              <div style={{ marginTop: 4 }}>
                <Text style={{ fontSize: 22, fontWeight: 700, color: '#52c41a' }}>{weightResult.weight.toFixed(3)}</Text>
                <Text type="secondary"> kg/m</Text>
                <Tag style={{ marginLeft: 8 }} color={weightResult.source === 'builtin' ? 'blue' : weightResult.source === 'computed' ? 'green' : 'default'}>
                  {weightResult.source === 'builtin' ? '国标内置' : weightResult.source === 'computed' ? '公式计算' : '未知'}
                </Tag>
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* ── batch list ──────────────────────────────────────────────── */}
      <Table
        rowKey="batch_id"
        dataSource={batches}
        loading={batchesQ.isLoading}
        size="middle"
        pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 个批次` }}
        locale={{ emptyText: '暂无处理批次，上传一个 Excel 文件开始' }}
        columns={[
          { title: '批次', dataIndex: 'batch_id', width: 60, align: 'center' as const },
          {
            title: '状态', width: 100,
            render: (_: unknown, r: ExcelFinalBatch) => {
              const s = STATUS[batchJobStatuses[r.batch_id]?.status] ?? STATUS.succeeded;
              return <Tag style={{ color: s.color, background: s.bg, border: 'none', borderRadius: 6 }}>{s.icon}<span style={{ marginLeft: 4 }}>{s.label}</span></Tag>;
            },
          },
          {
            title: '源文件', dataIndex: 'source_name',
            render: (v: string | null) => <Text ellipsis style={{ maxWidth: 300 }}>{v || '—'}</Text>,
          },
          {
            title: '格式', dataIndex: 'source_type', width: 80,
            render: (v: string) => <Tag>{v === 'init_table' ? '初始表' : v === 'tekla_tsv' ? 'Tekla TSV' : v}</Tag>,
          },
          { title: '零件数', dataIndex: 'part_count', width: 80, align: 'right' as const },
          { title: '构件数', dataIndex: 'component_count', width: 80, align: 'right' as const },
          {
            title: '净重(kg)', dataIndex: 'total_net_weight', width: 100, align: 'right' as const,
            render: (v: number | null) => v != null ? v.toFixed(1) : '—',
          },
          {
            title: '时间', dataIndex: 'created_at', width: 120,
            render: (v: string) => <Text type="secondary" style={{ fontSize: 13 }}>{v ? new Date(v).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}</Text>,
          },
          {
            title: '操作', width: 200, align: 'center' as const,
            render: (_: unknown, r: ExcelFinalBatch) => {
              const isDone = !(batchJobStatuses[r.batch_id]?.status === 'running' || batchJobStatuses[r.batch_id]?.status === 'queued');
              return (
                <Space size={2}>
                  <Tooltip title="查看详情">
                    <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => openDetail(r)} />
                  </Tooltip>
                  {isDone && (
                    <Tooltip title="下载处理结果">
                      <Button type="text" size="small" icon={<DownloadOutlined />}
                        onClick={() => handleDownloadResult(r.job_id, `零件清单_${r.source_name || r.batch_id}.xlsx`)} />
                    </Tooltip>
                  )}
                </Space>
              );
            },
          },
        ]}
      />

      {/* ── detail drawer ───────────────────────────────────────────── */}
      <Drawer
        title={detailBatch ? `批次 #${detailBatch.batch_id} · ${detailBatch.source_name || '详情'}` : '批次详情'}
        open={detailBatch !== null}
        onClose={() => { setDetailBatch(null); setDetailParts(null); setDetailComponents([]); }}
        width={720}
        loading={detailLoading}
      >
        {detailBatch && (
          <>
            <Descriptions column={2} size="small" bordered style={{ marginBottom: 20 }}>
              <Descriptions.Item label="源类型"><Tag>{detailBatch.source_type === 'init_table' ? '初始表' : detailBatch.source_type === 'tekla_tsv' ? 'Tekla TSV' : detailBatch.source_type}</Tag></Descriptions.Item>
              <Descriptions.Item label="源文件">{detailBatch.source_name || '—'}</Descriptions.Item>
              <Descriptions.Item label="零件总数">{detailBatch.part_count}</Descriptions.Item>
              <Descriptions.Item label="构件总数">{detailBatch.component_count}</Descriptions.Item>
              <Descriptions.Item label="净重合计">{detailBatch.total_net_weight != null ? `${detailBatch.total_net_weight.toFixed(1)} kg` : '—'}</Descriptions.Item>
              <Descriptions.Item label="毛重合计">{detailBatch.total_gross_weight != null ? `${detailBatch.total_gross_weight.toFixed(1)} kg` : '—'}</Descriptions.Item>
            </Descriptions>

            {/* Material breakdown */}
            {detailBatch.material_breakdown && detailBatch.material_breakdown.length > 0 && (
              <>
                <Title level={5}><PieChartOutlined /> 材质统计</Title>
                <Table
                  rowKey="material"
                  dataSource={detailBatch.material_breakdown.filter((m) => m.material)}
                  pagination={false}
                  size="small"
                  columns={[
                    { title: '材质', dataIndex: 'material' },
                    { title: '数量', dataIndex: 'count', align: 'right' as const },
                    { title: '净重(kg)', dataIndex: 'total_net_weight', align: 'right' as const, render: (v: number | null) => v != null ? v.toFixed(1) : '—' },
                  ]}
                  style={{ marginBottom: 16 }}
                />
              </>
            )}

            <Divider />

            {/* Parts table */}
            <Title level={5}><FileTextOutlined /> 零件清单</Title>
            <Space style={{ marginBottom: 12 }} wrap>
              <Input placeholder="规格" allowClear style={{ width: 120 }}
                value={partsFilters.spec || ''}
                onChange={(e) => setPartsFilters((f) => ({ ...f, spec: e.target.value }))} />
              <Input placeholder="材质" allowClear style={{ width: 100 }}
                value={partsFilters.material || ''}
                onChange={(e) => setPartsFilters((f) => ({ ...f, material: e.target.value }))} />
              <Input placeholder="零件号" allowClear style={{ width: 120 }}
                value={partsFilters.part_no || ''}
                onChange={(e) => setPartsFilters((f) => ({ ...f, part_no: e.target.value }))} />
              <Select placeholder="类型" allowClear style={{ width: 120 }}
                value={partsFilters.part_type || undefined}
                onChange={(v) => setPartsFilters((f) => ({ ...f, part_type: v || '' }))}
                options={[
                  { value: '零件', label: '零件' }, { value: 'BH腹', label: 'BH腹' }, { value: 'BH翼', label: 'BH翼' },
                  { value: 'BOX盖', label: 'BOX盖' }, { value: 'BOX腹', label: 'BOX腹' },
                ]} />
              <Button icon={<SearchOutlined />} onClick={() => { setPartsPage(1); loadPartsPage(1, partsFilters); }}>筛选</Button>
              <Button onClick={() => { setPartsFilters({}); setPartsPage(1); loadPartsPage(1, {}); }}>清除</Button>
            </Space>
            {detailParts && (
              <Table
                rowKey="id"
                dataSource={detailParts.data}
                size="small"
                scroll={{ x: 600 }}
                pagination={{
                  current: partsPage,
                  pageSize: 50,
                  total: detailParts.pagination?.total ?? 0,
                  onChange: (p) => { setPartsPage(p); loadPartsPage(p, partsFilters); },
                  showTotal: (t) => `共 ${t} 个零件`,
                }}
                columns={[
                  { title: '序号', dataIndex: 'seq', width: 50 },
                  { title: '构件号', dataIndex: 'component_no', width: 100, ellipsis: true },
                  { title: '零件类型', dataIndex: 'part_type', width: 80 },
                  {
                    title: '零件号', dataIndex: 'part_no', width: 100, ellipsis: true,
                    render: (v: string | null, r: ExcelFinalPart) => v
                      ? <a onClick={() => openPartDetail(detailBatch!.batch_id, r.id)}>{v}</a>
                      : '—',
                  },
                  { title: '规格', dataIndex: 'spec', width: 100, ellipsis: true },
                  { title: '材质', dataIndex: 'material', width: 80 },
                  { title: '宽', dataIndex: 'width', width: 60, align: 'right' as const, render: (v: number | null) => v ?? '—' },
                  { title: '长', dataIndex: 'length', width: 70, align: 'right' as const, render: (v: number | null) => v ?? '—' },
                  { title: '数量', dataIndex: 'qty', width: 60, align: 'right' as const },
                  { title: '净重', dataIndex: 'net_total_weight', width: 80, align: 'right' as const, render: (v: number | null) => v != null ? v.toFixed(1) : '—' },
                  { title: '毛重', dataIndex: 'gross_total_weight', width: 80, align: 'right' as const, render: (v: number | null) => v != null ? v.toFixed(1) : '—' },
                ]}
              />
            )}

            <Divider />

            {/* Components table */}
            <Title level={5}><BarChartOutlined /> 构件汇总</Title>
            <Table
              rowKey="id"
              dataSource={detailComponents}
              size="small"
              pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 个构件` }}
              columns={[
                { title: '构件号', dataIndex: 'component_no', ellipsis: true },
                { title: '数量', dataIndex: 'component_qty', width: 80, align: 'right' as const },
                { title: '总重(kg)', dataIndex: 'total_weight', width: 100, align: 'right' as const, render: (v: number | null) => v != null ? v.toFixed(1) : '—' },
              ]}
            />
          </>
        )}
      </Drawer>

      {/* ── Excel preview modal ──────────────────────────────────────── */}
      <ExcelPreview
        fileId={previewFileId}
        fileName={previewFileName}
        open={previewFileId !== null}
        onClose={() => { setPreviewFileId(null); setPreviewFileName(''); }}
      />

      {/* ── Part detail modal ────────────────────────────────────────── */}
      <Modal
        title={`零件详情 #${partDetail?.id ?? ''}`}
        open={partDetail !== null}
        onCancel={() => setPartDetail(null)}
        footer={null}
        width={640}
        loading={partDetailLoading}
      >
        {partDetail && (
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="批次">{partDetail.batch_id ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="序号">{partDetail.seq ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="构件号">{partDetail.component_no || '—'}</Descriptions.Item>
            <Descriptions.Item label="构件数">{partDetail.component_qty ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="零件类型">{partDetail.part_type || '—'}</Descriptions.Item>
            <Descriptions.Item label="零件号">{partDetail.part_no || '—'}</Descriptions.Item>
            <Descriptions.Item label="截面型材">{partDetail.profile_spec || '—'}</Descriptions.Item>
            <Descriptions.Item label="规格">{partDetail.spec || '—'}</Descriptions.Item>
            <Descriptions.Item label="宽度">{partDetail.width != null ? `${partDetail.width} mm` : '—'}</Descriptions.Item>
            <Descriptions.Item label="长度">{partDetail.length != null ? `${partDetail.length} mm` : '—'}</Descriptions.Item>
            <Descriptions.Item label="左进">{partDetail.left_inset != null ? `${partDetail.left_inset} mm` : '—'}</Descriptions.Item>
            <Descriptions.Item label="右进">{partDetail.right_inset != null ? `${partDetail.right_inset} mm` : '—'}</Descriptions.Item>
            <Descriptions.Item label="下料长度">{partDetail.cut_length != null ? `${partDetail.cut_length} mm` : '—'}</Descriptions.Item>
            <Descriptions.Item label="材质">{partDetail.material || '—'}</Descriptions.Item>
            <Descriptions.Item label="数量">{partDetail.qty ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="总数">{partDetail.total_qty ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="总长">{partDetail.total_length != null ? `${partDetail.total_length.toFixed(1)} mm` : '—'}</Descriptions.Item>
            <Descriptions.Item label="比重">{partDetail.density != null ? `${partDetail.density.toFixed(3)}` : '—'}</Descriptions.Item>
            <Descriptions.Item label="理单重">{partDetail.theo_unit_weight != null ? `${partDetail.theo_unit_weight.toFixed(3)} kg` : '—'}</Descriptions.Item>
            <Descriptions.Item label="理总重">{partDetail.theo_total_weight != null ? `${partDetail.theo_total_weight.toFixed(2)} kg` : '—'}</Descriptions.Item>
            <Descriptions.Item label="单净重">{partDetail.net_unit_weight != null ? `${partDetail.net_unit_weight.toFixed(3)} kg` : '—'}</Descriptions.Item>
            <Descriptions.Item label="总净重">{partDetail.net_total_weight != null ? `${partDetail.net_total_weight.toFixed(2)} kg` : '—'}</Descriptions.Item>
            <Descriptions.Item label="表净重">{partDetail.table_net_weight != null ? `${partDetail.table_net_weight.toFixed(2)} kg` : '—'}</Descriptions.Item>
            <Descriptions.Item label="单毛重">{partDetail.gross_unit_weight != null ? `${partDetail.gross_unit_weight.toFixed(3)} kg` : '—'}</Descriptions.Item>
            <Descriptions.Item label="总毛重">{partDetail.gross_total_weight != null ? `${partDetail.gross_total_weight.toFixed(2)} kg` : '—'}</Descriptions.Item>
            <Descriptions.Item label="表毛重">{partDetail.table_gross_weight != null ? `${partDetail.table_gross_weight.toFixed(2)} kg` : '—'}</Descriptions.Item>
            <Descriptions.Item label="单表面积">{partDetail.surface_area != null ? `${partDetail.surface_area.toFixed(3)} m²` : '—'}</Descriptions.Item>
            <Descriptions.Item label="总表面积">{partDetail.total_surface_area != null ? `${partDetail.total_surface_area.toFixed(3)} m²` : '—'}</Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </Space>
  );
}
