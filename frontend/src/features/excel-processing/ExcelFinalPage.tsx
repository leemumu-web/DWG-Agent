import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {
  CloudUploadOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileExcelOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';

import {
  getExcelFinalHealth,
  getExcelFinalOverview,
  getExcelFinalProcessStatus,
  listExcelFinalBatches,
  uploadAndProcessExcel,
} from './api';
import { downloadFile } from '../files';
import { describeApiError } from '../../shared/api';
import { listJobsPage, retryJob } from '../jobs';
import ExcelPreview from './ExcelPreview';
import type { ExcelFinalBatchSummary } from './types';
import type { Job } from '../jobs';
import { ExcelFinalBatchDrawer } from './components/ExcelFinalBatchDrawer';
import { ExcelFinalOverview } from './components/ExcelFinalOverview';
import { ExcelFinalTools } from './components/ExcelFinalTools';
import {
  DEFAULT_BATCH_PAGE_SIZE,
  mergeExcelFinalParams,
  omitDefault,
  parseExcelFinalUrlState,
} from './model/excelFinalUrlState';
import './components/ExcelFinalPage.css';

const TASK_TYPE = 'process_excel_final';
const ACTIVE_STATUSES = new Set(['queued', 'running']);
const STATUS_COLOR: Record<string, string> = {
  queued: 'warning',
  running: 'processing',
  succeeded: 'success',
  failed: 'error',
  cancelled: 'default',
};
const STATUS_TEXT: Record<string, string> = {
  queued: '等待中',
  running: '处理中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

function numberText(value: number | null | undefined, digits = 2): string {
  return value == null ? '-' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

export function ExcelFinalPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedRequestKey, setSelectedRequestKey] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [previewFileId, setPreviewFileId] = useState<number | null>(null);
  const [previewName, setPreviewName] = useState('');
  const urlState = useMemo(() => parseExcelFinalUrlState(searchParams), [searchParams]);
  const activeJobId = urlState.jobId;
  const batchPage = urlState.batchPage;
  const batchPageSize = urlState.batchPageSize;
  const selectedBatchId = urlState.batchId;

  const healthQ = useQuery({ queryKey: ['excel-final-health'], queryFn: getExcelFinalHealth, staleTime: 30_000 });
  const overviewQ = useQuery({ queryKey: ['excel-final-overview'], queryFn: getExcelFinalOverview, staleTime: 5_000 });
  const jobsQ = useQuery({
    queryKey: ['jobs', TASK_TYPE],
    queryFn: () => listJobsPage({ page: 1, page_size: 8, task_type: TASK_TYPE }),
    refetchInterval: (query) => query.state.data?.data.some((job) => ACTIVE_STATUSES.has(job.status)) ? 3000 : false,
  });
  const batchesQ = useQuery({
    queryKey: ['excel-final-batches', batchPage, batchPageSize],
    queryFn: () => listExcelFinalBatches(batchPage, batchPageSize),
    staleTime: 2_000,
  });
  const statusQ = useQuery({
    queryKey: ['excel-final-status', activeJobId],
    queryFn: () => getExcelFinalProcessStatus(activeJobId!),
    enabled: activeJobId !== null,
    refetchInterval: (query) => ACTIVE_STATUSES.has(query.state.data?.status ?? '') ? 2000 : false,
  });

  useEffect(() => {
    const status = statusQ.data?.status;
    if (!status || ACTIVE_STATUSES.has(status)) return;
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ['jobs', TASK_TYPE] }),
      queryClient.invalidateQueries({ queryKey: ['excel-final-batches'] }),
      queryClient.invalidateQueries({ queryKey: ['excel-final-overview'] }),
    ]);
  }, [queryClient, statusQ.data?.status]);

  const recentJobs = useMemo(() => jobsQ.data?.data ?? [], [jobsQ.data]);
  const refreshedAt = Math.max(
    healthQ.dataUpdatedAt,
    overviewQ.dataUpdatedAt,
    jobsQ.dataUpdatedAt,
    batchesQ.dataUpdatedAt,
  );

  function updateUrl(changes: Record<string, string | number | null | undefined>) {
    setSearchParams(mergeExcelFinalParams(searchParams, changes));
  }

  function trackJob(jobId: number) {
    updateUrl({ job_id: jobId });
  }

  async function submit() {
    if (!selectedFile || !selectedRequestKey) return;
    if (!/\.xlsx?$/i.test(selectedFile.name)) {
      message.error('请选择 .xlsx 或 .xls 文件');
      return;
    }
    setSubmitting(true);
    try {
      const result = await uploadAndProcessExcel(selectedFile, selectedRequestKey);
      setSelectedFile(null);
      setSelectedRequestKey(null);
      trackJob(result.job_id);
      message.success(
        result.reused
          ? `任务 #${result.job_id} 已存在，已继续跟踪`
          : `任务 #${result.job_id} 已提交`,
      );
      await queryClient.invalidateQueries({ queryKey: ['jobs', TASK_TYPE] });
    } catch (error) {
      message.error(describeApiError(error, '提交失败'));
    } finally {
      setSubmitting(false);
    }
  }

  async function loadJobResult(jobId: number) {
    const status = await getExcelFinalProcessStatus(jobId);
    if (!status.result_file_id) throw new Error('结果文件尚未生成');
    return status.result_file_id;
  }

  async function downloadJobResult(jobId: number) {
    try {
      const fileId = await loadJobResult(jobId);
      await downloadFile(fileId, `excel-final-${jobId}.xlsx`);
    } catch (error) {
      message.error(describeApiError(error, '下载失败'));
    }
  }

  async function previewJobResult(jobId: number) {
    try {
      const fileId = statusQ.data?.job_id === jobId && statusQ.data.result_file_id
        ? statusQ.data.result_file_id
        : await loadJobResult(jobId);
      setPreviewFileId(fileId);
      setPreviewName(`excel-final-${jobId}.xlsx`);
    } catch (error) {
      message.error(describeApiError(error, '预览失败'));
    }
  }

  async function retry(jobId: number) {
    try {
      await retryJob(jobId);
      updateUrl({ batch_id: null });
      trackJob(jobId);
      message.success(`任务 #${jobId} 已重新提交`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['jobs', TASK_TYPE] }),
        queryClient.invalidateQueries({ queryKey: ['excel-final-status', jobId], exact: true, refetchType: 'all' }),
      ]);
    } catch (error) {
      message.error(describeApiError(error, '重试失败'));
    }
  }

  async function refreshAll() {
    await Promise.all([healthQ.refetch(), overviewQ.refetch(), jobsQ.refetch(), batchesQ.refetch()]);
  }

  const jobColumns = [
    { title: '任务', dataIndex: 'id', width: 90, render: (id: number) => <button className="excel-final-job-link" onClick={() => trackJob(id)}>#{id}</button> },
    { title: '源文件', dataIndex: 'params_json', ellipsis: true, render: (params: Record<string, unknown> | null) => `文件 #${params?.file_id ?? '-'}` },
    { title: '状态', dataIndex: 'status', width: 100, render: (status: string) => <Tag color={STATUS_COLOR[status]}>{STATUS_TEXT[status] ?? status}</Tag> },
    {
      title: '进度', dataIndex: 'progress', width: 170,
      render: (progress: number, job: Job) => <Progress percent={progress} size="small" status={job.status === 'failed' ? 'exception' : job.status === 'succeeded' ? 'success' : undefined} />,
    },
    { title: '创建时间', dataIndex: 'created_at', width: 180, render: (value: string) => new Date(value).toLocaleString('zh-CN') },
    {
      title: '操作', key: 'actions', width: 130, fixed: 'right' as const,
      render: (_: unknown, job: Job) => (
        <Space size={2}>
          <Button type="text" icon={<EyeOutlined />} aria-label={`预览任务 ${job.id} 结果`} disabled={job.status !== 'succeeded'} onClick={() => void previewJobResult(job.id)} />
          <Button type="text" icon={<DownloadOutlined />} title="下载结果" aria-label={`下载任务 ${job.id} 结果`} disabled={job.status !== 'succeeded'} onClick={() => void downloadJobResult(job.id)} />
          {(job.status === 'failed' || job.status === 'cancelled') && <Button type="text" icon={<PlayCircleOutlined />} title="重新提交" aria-label={`重新提交任务 ${job.id}`} onClick={() => void retry(job.id)} />}
        </Space>
      ),
    },
  ];
  const batchColumns = [
    { title: '批次', dataIndex: 'batch_id', width: 90, render: (id: number) => `#${id}` },
    { title: '来源文件', dataIndex: 'source_name', ellipsis: true },
    { title: '格式', dataIndex: 'source_type', width: 110, render: (value: string) => <Tag>{value}</Tag> },
    { title: '零件', dataIndex: 'part_count', width: 90 },
    { title: '构件', dataIndex: 'component_count', width: 90 },
    { title: '净重 / kg', dataIndex: 'total_net_weight', width: 130, render: (value: number | null) => numberText(value) },
    { title: '毛重 / kg', dataIndex: 'total_gross_weight', width: 130, render: (value: number | null) => numberText(value) },
    { title: '入库时间', dataIndex: 'created_at', width: 180, render: (value: string | null) => value ? new Date(value).toLocaleString('zh-CN') : '-' },
    {
      title: '', key: 'actions', width: 56, fixed: 'right' as const,
      render: (_: unknown, batch: ExcelFinalBatchSummary) => <Button type="text" icon={<EyeOutlined />} aria-label={`查看批次 ${batch.batch_id}`} onClick={() => updateUrl({ batch_id: batch.batch_id })} />,
    },
  ];

  const activeStatus = statusQ.data;
  return (
    <div className="excel-final-page">
      <header className="excel-final-header">
        <div>
          <span className="excel-final-kicker">PART LIST / DATA OBSERVATORY</span>
          <Typography.Title level={2}>Excel Final 数据控制台</Typography.Title>
          <Typography.Paragraph className="excel-final-description">监视处理任务、核对业务数据库入库记录，并预览对象存储中的最终清单。</Typography.Paragraph>
        </div>
        <div className="excel-final-header-actions">
          <span className="excel-final-refreshed" aria-live="polite">
            最近刷新 {refreshedAt ? new Date(refreshedAt).toLocaleTimeString('zh-CN') : '等待数据'}
          </span>
          <Button icon={<ReloadOutlined />} loading={healthQ.isFetching || overviewQ.isFetching || batchesQ.isFetching} onClick={() => void refreshAll()}>刷新数据</Button>
        </div>
      </header>

      <ExcelFinalOverview
        health={healthQ.data}
        overview={overviewQ.data}
        loading={healthQ.isLoading || overviewQ.isLoading}
        error={healthQ.isError || overviewQ.isError ? describeApiError(healthQ.error ?? overviewQ.error, '无法读取数据概览') : undefined}
      />

      <section className="excel-final-ingest" aria-label="Excel 文件入库">
        <div className="excel-final-ingest-copy">
          <span className="excel-final-ingest-icon"><CloudUploadOutlined /></span>
          <div><strong>登记并处理 Excel 清单</strong><span>上传对象写入已配置存储，文件与传输流水同步登记到业务数据库</span></div>
        </div>
        <div className="excel-final-ingest-actions">
          <Upload accept=".xlsx,.xls" maxCount={1} fileList={selectedFile ? [{ uid: 'selected', name: selectedFile.name, status: 'done' }] : []}
            beforeUpload={(file) => {
              setSelectedFile(file);
              setSelectedRequestKey(crypto.randomUUID());
              return false;
            }}
            onRemove={() => {
              setSelectedFile(null);
              setSelectedRequestKey(null);
              return true;
            }}>
            <Button icon={<FileExcelOutlined />}>选择 Excel</Button>
          </Upload>
          <Button type="primary" icon={<PlayCircleOutlined />} disabled={!selectedFile} loading={submitting} onClick={() => void submit()}>提交处理</Button>
        </div>
      </section>

      {statusQ.isError && <Alert type="error" showIcon message={`任务 #${activeJobId} 状态读取失败`} description={describeApiError(statusQ.error, '任务可能不存在或无权访问')} />}
      {activeStatus && (
        <section className={`excel-final-active-job is-${activeStatus.status}`} aria-label={`任务 ${activeStatus.job_id} 状态`}>
          <div className="excel-final-active-head">
            <Space><span className="excel-final-pulse" /><strong>任务 #{activeStatus.job_id}</strong><Tag color={STATUS_COLOR[activeStatus.status]}>{STATUS_TEXT[activeStatus.status] ?? activeStatus.status}</Tag></Space>
            <Space>
              {activeStatus.result_file_id && <Button icon={<EyeOutlined />} aria-label={`预览任务 ${activeStatus.job_id} 结果`} onClick={() => void previewJobResult(activeStatus.job_id)}>预览结果</Button>}
              {activeStatus.result_file_id && <Button icon={<DownloadOutlined />} onClick={() => void downloadJobResult(activeStatus.job_id)}>下载结果</Button>}
            </Space>
          </div>
          <Progress percent={activeStatus.progress} status={activeStatus.status === 'failed' ? 'exception' : activeStatus.status === 'succeeded' ? 'success' : 'active'} />
          {activeStatus.error_message && <Alert type="error" showIcon message={activeStatus.error_message} />}
        </section>
      )}

      <ExcelFinalTools />

      <section className="excel-final-data-section">
        <div className="excel-final-section-head"><div><span>DATABASE RECORDS</span><Typography.Title level={4}>处理批次</Typography.Title></div><small>精确总数 · 服务端分页 · 权限过滤</small></div>
        {batchesQ.isError && <Alert type="error" showIcon message="批次列表加载失败" description={describeApiError(batchesQ.error, '请检查数据库连接')} />}
        <Table<ExcelFinalBatchSummary>
          rowKey="batch_id" size="middle" loading={batchesQ.isLoading} dataSource={batchesQ.data?.data ?? []}
          columns={batchColumns} scroll={{ x: 1120 }}
          pagination={{ current: batchPage, pageSize: batchPageSize, total: batchesQ.data?.pagination.total ?? 0, showSizeChanger: true, showTotal: (total) => `共 ${total} 个批次` }}
          onChange={(pagination) => {
            const nextSize = pagination.pageSize ?? DEFAULT_BATCH_PAGE_SIZE;
            const sizeChanged = nextSize !== batchPageSize;
            const nextPage = sizeChanged ? 1 : (pagination.current ?? 1);
            updateUrl({
              batch_page: omitDefault(nextPage, 1),
              batch_size: omitDefault(nextSize, DEFAULT_BATCH_PAGE_SIZE),
            });
          }}
        />
      </section>

      <section className="excel-final-data-section">
        <div className="excel-final-section-head"><div><span>EXECUTION LEDGER</span><Typography.Title level={4}>近期任务</Typography.Title></div><small>仅轮询活动任务，避免批次 N+1 请求</small></div>
        {jobsQ.isError && <Alert type="error" showIcon message="任务列表加载失败" description={describeApiError(jobsQ.error, '请稍后重试')} />}
        <Table<Job> rowKey="id" size="small" loading={jobsQ.isLoading} dataSource={recentJobs} columns={jobColumns} pagination={false} scroll={{ x: 850 }} />
      </section>

      <ExcelFinalBatchDrawer batchId={selectedBatchId} open={selectedBatchId !== null} onClose={() => updateUrl({ batch_id: null })} />
      <ExcelPreview fileId={previewFileId} fileName={previewName} open={previewFileId !== null} onClose={() => setPreviewFileId(null)} />
    </div>
  );
}
