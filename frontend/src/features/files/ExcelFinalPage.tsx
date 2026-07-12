import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  App,
  Button,
  Descriptions,
  Drawer,
  Flex,
  Input,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {
  DownloadOutlined,
  EyeOutlined,
  FileExcelOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getExcelFinalBatch,
  getExcelFinalProcessStatus,
  listExcelFinalBatches,
  listExcelFinalParts,
  uploadAndProcessExcel,
  type ExcelFinalPartFilters,
} from '../../api/excel-final.api';
import { downloadFile } from '../../api/files.api';
import { listJobsPage, retryJob } from '../../api/jobs.api';
import type { ExcelFinalBatchSummary, ExcelFinalPart } from '../../types/excel-final';
import type { Job } from '../../types/job';

const TASK_TYPE = 'process_excel_final';
const ACTIVE_STATUSES = new Set(['queued', 'running']);
const STATUS_COLOR: Record<string, string> = {
  queued: 'warning',
  running: 'processing',
  succeeded: 'success',
  failed: 'error',
  cancelled: 'default',
};

function errorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const message = (error.response?.data as { error?: { message?: string } } | undefined)
      ?.error?.message;
    if (message) return message;
  }
  return error instanceof Error ? error.message : fallback;
}

function numberText(value: number | null | undefined, digits = 2): string {
  return value == null ? '-' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

export function ExcelFinalPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [batchPage, setBatchPage] = useState(1);
  const [batchPageSize, setBatchPageSize] = useState(20);
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(null);
  const [partPage, setPartPage] = useState(1);
  const [partPageSize, setPartPageSize] = useState(50);
  const [filterDraft, setFilterDraft] = useState<ExcelFinalPartFilters>({});
  const [partFilters, setPartFilters] = useState<ExcelFinalPartFilters>({});

  const jobsQ = useQuery({
    queryKey: ['jobs', TASK_TYPE],
    queryFn: () => listJobsPage({ page: 1, page_size: 8, task_type: TASK_TYPE }),
    refetchInterval: (query) =>
      query.state.data?.data.some((job) => ACTIVE_STATUSES.has(job.status)) ? 3000 : false,
  });
  const batchesQ = useQuery({
    queryKey: ['excel-final-batches', batchPage, batchPageSize],
    queryFn: () => listExcelFinalBatches(batchPage, batchPageSize),
    staleTime: 2000,
  });
  const statusQ = useQuery({
    queryKey: ['excel-final-status', activeJobId],
    queryFn: () => getExcelFinalProcessStatus(activeJobId!),
    enabled: activeJobId !== null,
    refetchInterval: (query) =>
      ACTIVE_STATUSES.has(query.state.data?.status ?? '') ? 2000 : false,
  });
  const batchDetailQ = useQuery({
    queryKey: ['excel-final-batch', selectedBatchId],
    queryFn: () => getExcelFinalBatch(selectedBatchId!),
    enabled: selectedBatchId !== null,
  });
  const partsQ = useQuery({
    queryKey: [
      'excel-final-parts',
      selectedBatchId,
      partPage,
      partPageSize,
      partFilters,
    ],
    queryFn: () =>
      listExcelFinalParts(selectedBatchId!, partPage, partPageSize, partFilters),
    enabled: selectedBatchId !== null,
  });

  useEffect(() => {
    const status = statusQ.data?.status;
    if (status && !ACTIVE_STATUSES.has(status)) {
      queryClient.invalidateQueries({ queryKey: ['jobs', TASK_TYPE] });
      queryClient.invalidateQueries({ queryKey: ['excel-final-batches'] });
    }
  }, [queryClient, statusQ.data?.status]);

  const recentJobs = useMemo(() => jobsQ.data?.data ?? [], [jobsQ.data]);

  async function submit() {
    if (!selectedFile) return;
    const lower = selectedFile.name.toLowerCase();
    if (!lower.endsWith('.xlsx') && !lower.endsWith('.xls')) {
      message.error('请选择 .xlsx 或 .xls 文件');
      return;
    }
    setSubmitting(true);
    try {
      const result = await uploadAndProcessExcel(selectedFile);
      setActiveJobId(result.job_id);
      setSelectedFile(null);
      message.success(`任务 #${result.job_id} 已提交`);
      await queryClient.invalidateQueries({ queryKey: ['jobs', TASK_TYPE] });
    } catch (error) {
      message.error(errorMessage(error, '提交失败'));
    } finally {
      setSubmitting(false);
    }
  }

  async function downloadJobResult(jobId: number) {
    try {
      const status = await getExcelFinalProcessStatus(jobId);
      if (!status.result_file_id) {
        message.warning('结果文件尚未生成');
        return;
      }
      await downloadFile(status.result_file_id, `excel-final-${jobId}.xlsx`);
    } catch (error) {
      message.error(errorMessage(error, '下载失败'));
    }
  }

  async function retry(jobId: number) {
    try {
      await retryJob(jobId);
      setActiveJobId(jobId);
      setSelectedBatchId(null);
      message.success(`任务 #${jobId} 已重新提交`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['jobs', TASK_TYPE] }),
        queryClient.invalidateQueries({
          queryKey: ['excel-final-status', jobId],
          exact: true,
          refetchType: 'all',
        }),
      ]);
    } catch (error) {
      message.error(errorMessage(error, '重试失败'));
    }
  }

  function openBatch(batchId: number) {
    setSelectedBatchId(batchId);
    setPartPage(1);
    setFilterDraft({});
    setPartFilters({});
  }

  function applyPartFilters() {
    setPartPage(1);
    setPartFilters(
      Object.fromEntries(
        Object.entries(filterDraft).filter(([, value]) => value?.trim()),
      ) as ExcelFinalPartFilters,
    );
  }

  const jobColumns = [
    { title: '任务', dataIndex: 'id', width: 80, render: (id: number) => `#${id}` },
    {
      title: '源文件',
      dataIndex: 'params_json',
      ellipsis: true,
      render: (params: Record<string, unknown> | null) => `文件 #${params?.file_id ?? '-'}`,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (status: string) => <Tag color={STATUS_COLOR[status]}>{status}</Tag>,
    },
    {
      title: '进度',
      dataIndex: 'progress',
      width: 180,
      render: (progress: number, job: Job) => (
        <Progress
          percent={progress}
          size="small"
          status={job.status === 'failed' ? 'exception' : job.status === 'succeeded' ? 'success' : undefined}
        />
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 180,
      render: (value: string) => new Date(value).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      width: 100,
      render: (_: unknown, job: Job) => (
        <Space size={4}>
          <Button
            type="text"
            icon={<DownloadOutlined />}
            title="下载结果"
            disabled={job.status !== 'succeeded'}
            onClick={() => downloadJobResult(job.id)}
          />
          {(job.status === 'failed' || job.status === 'cancelled') && (
            <Button
              type="text"
              icon={<PlayCircleOutlined />}
              title="重新提交"
              onClick={() => retry(job.id)}
            />
          )}
        </Space>
      ),
    },
  ];

  const batchColumns = [
    { title: '批次', dataIndex: 'batch_id', width: 80, render: (id: number) => `#${id}` },
    { title: '源文件', dataIndex: 'source_name', ellipsis: true },
    { title: '格式', dataIndex: 'source_type', width: 110 },
    { title: '零件', dataIndex: 'part_count', width: 90 },
    { title: '构件', dataIndex: 'component_count', width: 90 },
    {
      title: '净重 (kg)',
      dataIndex: 'total_net_weight',
      width: 130,
      render: (value: number | null) => numberText(value),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 180,
      render: (value: string | null) => value ? new Date(value).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作',
      width: 110,
      render: (_: unknown, batch: ExcelFinalBatchSummary) => (
        <Space size={4}>
          <Button
            type="text"
            icon={<EyeOutlined />}
            title="查看批次"
            onClick={() => openBatch(batch.batch_id)}
          />
          <Button
            type="text"
            icon={<DownloadOutlined />}
            title="下载结果"
            onClick={() => downloadJobResult(batch.job_id)}
          />
        </Space>
      ),
    },
  ];

  const partColumns = [
    { title: '序号', dataIndex: 'seq', width: 70 },
    { title: '构件号', dataIndex: 'component_no', width: 120 },
    { title: '零件号', dataIndex: 'part_no', width: 130 },
    { title: '类型', dataIndex: 'part_type', width: 100 },
    { title: '规格', dataIndex: 'spec', width: 130 },
    { title: '材质', dataIndex: 'material', width: 100 },
    { title: '宽度', dataIndex: 'width', width: 90, render: (value: number | null) => numberText(value) },
    { title: '长度', dataIndex: 'length', width: 100, render: (value: number | null) => numberText(value) },
    { title: '数量', dataIndex: 'qty', width: 80, render: (value: number | null) => numberText(value) },
    {
      title: '理论总重',
      dataIndex: 'theo_total_weight',
      width: 110,
      render: (value: number | null) => numberText(value),
    },
    {
      title: '净总重',
      dataIndex: 'net_total_weight',
      width: 110,
      render: (value: number | null) => numberText(value),
    },
  ];

  const activeStatus = statusQ.data;

  return (
    <div>
      <Flex justify="space-between" align="center" gap={16} wrap="wrap" style={{ marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>Excel 零件清单</Typography.Title>
        <Button
          icon={<ReloadOutlined />}
          title="刷新"
          onClick={() => {
            jobsQ.refetch();
            batchesQ.refetch();
          }}
        />
      </Flex>

      <Flex
        align="center"
        gap={12}
        wrap="wrap"
        style={{ background: '#fff', border: '1px solid #f0f0f0', padding: 16, marginBottom: 16 }}
      >
        <FileExcelOutlined style={{ fontSize: 24, color: '#389e0d' }} />
        <Upload
          accept=".xlsx,.xls"
          maxCount={1}
          beforeUpload={(file) => {
            setSelectedFile(file);
            return false;
          }}
          onRemove={() => setSelectedFile(null)}
        >
          <Button icon={<UploadOutlined />}>选择 Excel</Button>
        </Upload>
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          disabled={!selectedFile}
          loading={submitting}
          onClick={submit}
        >
          提交处理
        </Button>
      </Flex>

      {activeStatus && (
        <div style={{ background: '#fff', border: '1px solid #f0f0f0', padding: 16, marginBottom: 16 }}>
          <Flex align="center" justify="space-between" gap={16} wrap="wrap">
            <Space>
              <Typography.Text strong>任务 #{activeStatus.job_id}</Typography.Text>
              <Tag color={STATUS_COLOR[activeStatus.status]}>{activeStatus.status}</Tag>
            </Space>
            {activeStatus.result_file_id && (
              <Button
                icon={<DownloadOutlined />}
                onClick={() => downloadJobResult(activeStatus.job_id)}
              >
                下载结果
              </Button>
            )}
          </Flex>
          <Progress
            percent={activeStatus.progress}
            status={activeStatus.status === 'failed' ? 'exception' : activeStatus.status === 'succeeded' ? 'success' : 'active'}
            style={{ marginTop: 12 }}
          />
          {activeStatus.error_message && (
            <Typography.Text type="danger">{activeStatus.error_message}</Typography.Text>
          )}
        </div>
      )}

      <Typography.Title level={5}>近期任务</Typography.Title>
      <Table<Job>
        rowKey="id"
        size="small"
        loading={jobsQ.isLoading}
        dataSource={recentJobs}
        columns={jobColumns}
        pagination={false}
        scroll={{ x: 760 }}
        style={{ marginBottom: 20 }}
      />

      <Typography.Title level={5}>处理批次</Typography.Title>
      <Table<ExcelFinalBatchSummary>
        rowKey="batch_id"
        size="small"
        loading={batchesQ.isLoading}
        dataSource={batchesQ.data?.data ?? []}
        columns={batchColumns}
        scroll={{ x: 900 }}
        pagination={{
          current: batchPage,
          pageSize: batchPageSize,
          total: batchesQ.data?.pagination.total ?? 0,
          showSizeChanger: true,
        }}
        onChange={(pagination) => {
          setBatchPage(pagination.current ?? 1);
          setBatchPageSize(pagination.pageSize ?? 20);
        }}
      />

      <Drawer
        title={`批次 #${selectedBatchId ?? ''}`}
        open={selectedBatchId !== null}
        size="min(1100px, 94vw)"
        onClose={() => setSelectedBatchId(null)}
      >
        {batchDetailQ.data && (
          <>
            <Descriptions
              size="small"
              column={{ xs: 1, sm: 2, md: 3 }}
              items={[
                { key: 'source', label: '源文件', children: batchDetailQ.data.source_name ?? '-' },
                { key: 'parts', label: '零件数', children: batchDetailQ.data.part_count },
                { key: 'components', label: '构件数', children: batchDetailQ.data.component_count },
                { key: 'net', label: '净重 (kg)', children: numberText(batchDetailQ.data.total_net_weight) },
                { key: 'gross', label: '毛重 (kg)', children: numberText(batchDetailQ.data.total_gross_weight) },
                {
                  key: 'materials',
                  label: '材质',
                  children: (
                    <Space size={[4, 4]} wrap>
                      {batchDetailQ.data.material_breakdown.map((item) => (
                        <Tag key={item.material}>{item.material} · {item.count}</Tag>
                      ))}
                    </Space>
                  ),
                },
              ]}
            />

            <Flex gap={8} wrap="wrap" style={{ margin: '20px 0 12px' }}>
              <Input
                value={filterDraft.part_no}
                placeholder="零件号"
                allowClear
                style={{ width: 160 }}
                onChange={(event) => setFilterDraft((value) => ({ ...value, part_no: event.target.value }))}
                onPressEnter={applyPartFilters}
              />
              <Input
                value={filterDraft.spec}
                placeholder="规格"
                allowClear
                style={{ width: 160 }}
                onChange={(event) => setFilterDraft((value) => ({ ...value, spec: event.target.value }))}
                onPressEnter={applyPartFilters}
              />
              <Input
                value={filterDraft.material}
                placeholder="材质"
                allowClear
                style={{ width: 140 }}
                onChange={(event) => setFilterDraft((value) => ({ ...value, material: event.target.value }))}
                onPressEnter={applyPartFilters}
              />
              <Button type="primary" onClick={applyPartFilters}>筛选</Button>
            </Flex>

            <Table<ExcelFinalPart>
              rowKey="id"
              size="small"
              loading={partsQ.isLoading}
              dataSource={partsQ.data?.data ?? []}
              columns={partColumns}
              scroll={{ x: 1200 }}
              pagination={{
                current: partPage,
                pageSize: partPageSize,
                total: partsQ.data?.pagination.total ?? 0,
                showSizeChanger: true,
              }}
              onChange={(pagination) => {
                setPartPage(pagination.current ?? 1);
                setPartPageSize(pagination.pageSize ?? 50);
              }}
            />
          </>
        )}
      </Drawer>
    </div>
  );
}
