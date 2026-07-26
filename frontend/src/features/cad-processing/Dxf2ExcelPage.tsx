import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Checkbox,
  Popconfirm,
  message,
  Space,
  Typography,
  Alert,
} from 'antd';
import {
  DeleteOutlined,
  DownloadOutlined,
  FolderOpenOutlined,
  CheckCircleFilled,
  SyncOutlined,
  CloudOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  listBatches,
  downloadFile,
  deleteBatch,
  downloadBatchZip,
} from '../files';
import { ExcelPreview, processExcelFinalFile } from '../excel-processing';
import { cancelJob, createDxf2ExcelJob, getJobResults, listJobsPage, retryJob } from '../jobs';
import type { Job } from '../jobs';
import { DxfBatchCard } from './components/dxf2excel/DxfBatchCard';
import { DxfUploadPanel } from './components/dxf2excel/DxfUploadPanel';
import { describeApiError, type TransferProgress } from '../../shared/api';
import { TransferProgressBar } from '../../shared/components';

// ── page ──────────────────────────────────────────────────────────────────────

export function Dxf2ExcelPage() {
  const navigate = useNavigate();
  const finalSubmissionRef = useRef<Set<string>>(new Set());

  // Multi-select state
  const [selectedBatches, setSelectedBatches] = useState<Set<string>>(new Set());
  // Batches whose cancelled/failed jobs have been explicitly cleared by the user
  const [clearedBatches, setClearedBatches] = useState<Set<string>>(new Set());
  // Excel preview modal state
  const [previewFileId, setPreviewFileId] = useState<number | null>(null);
  const [previewFileName, setPreviewFileName] = useState<string>('');
  const [finalSubmittingBatches, setFinalSubmittingBatches] = useState<Set<string>>(new Set());
  const [batchDownloadBusy, setBatchDownloadBusy] = useState(false);
  const [batchDownloadProgress, setBatchDownloadProgress] = useState<{
    label: string;
    progress: TransferProgress;
  } | null>(null);

  // ── data ──────────────────────────────────────────────────────────────────
  const batchesQ = useQuery({
    queryKey: ['batches', '.dxf'],
    queryFn: ({ queryKey }) => listBatches(queryKey[1] as string),
    staleTime: 3000,
  });
  const jobsQ = useQuery({
    queryKey: ['jobs', 'extract_dxf_to_excel'],
    queryFn: ({ queryKey }) => listJobsPage({ page: 1, page_size: 200, task_type: queryKey[1] as string }),
    staleTime: 2000,
  });

  const batches = batchesQ.data ?? [];
  const allJobs = jobsQ.data?.data ?? [];

  const hasActive = useMemo(
    () => allJobs.some((j) => j.status === 'queued' || j.status === 'running'),
    [allJobs],
  );

  // Smart polling
  useEffect(() => {
    if (!hasActive) return;
    const id = setInterval(() => { batchesQ.refetch(); jobsQ.refetch(); }, 3000);
    return () => clearInterval(id);
  }, [hasActive, batchesQ, jobsQ]);

  // batch_name → latest Job (skip cleared batches)
  const jobsByBatch = useMemo(() => {
    const map = new Map<string, Job>();
    for (const j of allJobs) {
      const bn = (j.params_json as Record<string, unknown> | null)?.batch_name as string | undefined;
      if (bn && !map.has(bn) && !clearedBatches.has(bn)) map.set(bn, j);
    }
    return map;
  }, [allJobs, clearedBatches]);

  const refresh = useCallback(() => { batchesQ.refetch(); jobsQ.refetch(); }, [batchesQ, jobsQ]);

  // ── stats ─────────────────────────────────────────────────────────────────
  const succeededCount = batches.filter((b) => jobsByBatch.get(b.name)?.status === 'succeeded').length;
  const processingCount = batches.filter((b) => {
    const s = jobsByBatch.get(b.name)?.status; return s === 'running' || s === 'queued';
  }).length;
  const totalFiles = batches.reduce((s, b) => s + b.file_count, 0);

  // ── multi-select ──────────────────────────────────────────────────────────
  const selectedArr = Array.from(selectedBatches);
  const allSelected = batches.length > 0 && selectedBatches.size === batches.length;
  const someSelected = selectedBatches.size > 0 && !allSelected;

  const toggleSelectAll = () => {
    if (allSelected || someSelected) {
      setSelectedBatches(new Set());
    } else {
      setSelectedBatches(new Set(batches.map((b) => b.name)));
    }
  };

  const toggleBatch = (name: string) => {
    setSelectedBatches((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  // ── single-batch actions ──────────────────────────────────────────────────
  const handleProcess = useCallback(async (batchName: string) => {
    try {
      await createDxf2ExcelJob(batchName);
      setClearedBatches((prev) => {
        const next = new Set(prev);
        next.delete(batchName);
        return next;
      });
      message.success(`已提交提取任务: ${batchName}`);
      refresh();
    } catch (err) { message.error(describeApiError(err, '提交失败')); }
  }, [refresh]);

  const handlePreviewExcel = useCallback(async (batchName: string) => {
    try {
      const job = jobsByBatch.get(batchName);
      if (!job) { message.error('未找到提取任务'); return; }
      const results = await getJobResults(job.id);
      const excel = results.find((r) => r.result_type === 'extract_dxf_to_excel');
      if (!excel?.result_file_id) { message.error('Excel 结果未找到'); return; }
      setPreviewFileId(excel.result_file_id);
      setPreviewFileName(`${batchName}.xlsx`);
    } catch (err) { message.error(describeApiError(err, '获取预览失败')); }
  }, [jobsByBatch]);

  const handleDownloadExcel = useCallback(async (batchName: string) => {
    try {
      const job = jobsByBatch.get(batchName);
      if (!job) { message.error('未找到提取任务'); return; }
      const results = await getJobResults(job.id);
      const excel = results.find((r) => r.result_type === 'extract_dxf_to_excel');
      if (!excel?.result_file_id) { message.error('Excel 结果未找到'); return; }
      await downloadFile(excel.result_file_id, `${batchName}.xlsx`);
    } catch (err) { message.error(describeApiError(err, '下载失败')); }
  }, [jobsByBatch]);

  const handleProcessExcelFinal = useCallback(async (batchName: string) => {
    if (finalSubmissionRef.current.has(batchName)) return;
    finalSubmissionRef.current.add(batchName);
    setFinalSubmittingBatches((current) => new Set(current).add(batchName));
    try {
      const extractionJob = jobsByBatch.get(batchName);
      if (!extractionJob || extractionJob.status !== 'succeeded') {
        throw new Error('DXF 提取任务尚未完成');
      }
      const results = await getJobResults(extractionJob.id);
      const excel = results.find((result) => result.result_type === 'extract_dxf_to_excel');
      if (!excel?.result_file_id) throw new Error('Excel 结果文件未找到');
      const requestKey = `dxf2excel-${extractionJob.id}-${excel.result_file_id}`;
      const finalJob = await processExcelFinalFile(excel.result_file_id, requestKey);
      message.success(
        finalJob.reused
          ? `零件清单任务 #${finalJob.job_id} 已存在，已继续跟踪`
          : `零件清单任务 #${finalJob.job_id} 已登记`,
      );
      navigate(`/files/excel-final?job_id=${finalJob.job_id}`);
    } catch (err) {
      message.error(describeApiError(err, '零件清单任务登记失败'));
    } finally {
      finalSubmissionRef.current.delete(batchName);
      setFinalSubmittingBatches((current) => {
        const next = new Set(current);
        next.delete(batchName);
        return next;
      });
    }
  }, [jobsByBatch, navigate]);

  const handleRetry = useCallback(async (batchName: string) => {
    try {
      const job = jobsByBatch.get(batchName);
      if (!job) { message.error('未找到提取任务'); return; }
      await retryJob(job.id);
      message.success('已重新提交');
      refresh();
    } catch (err) { message.error(describeApiError(err, '重试失败')); }
  }, [jobsByBatch, refresh]);

  const handleCancel = useCallback(async (batchName: string) => {
    try {
      const job = jobsByBatch.get(batchName);
      if (!job) { message.error('未找到提取任务'); return; }
      await cancelJob(job.id);
      message.success(`已暂停: ${batchName}（可点击"重试提取"重新开始）`);
      refresh();
    } catch (err) { message.error(describeApiError(err, '暂停失败')); }
  }, [jobsByBatch, refresh]);

  const handleClear = useCallback(async (batchName: string) => {
    try {
      const job = jobsByBatch.get(batchName);
      if (job && (job.status === 'queued' || job.status === 'running')) {
        try { await cancelJob(job.id); } catch { /* already terminal */ }
      }
      setClearedBatches((prev) => {
        const next = new Set(prev);
        next.add(batchName);
        return next;
      });
      message.success(`已取消: ${batchName}`);
    } catch (err) { message.error(describeApiError(err, '取消失败')); }
  }, [jobsByBatch]);

  const handleDeleteBatch = useCallback(async (batchName: string) => {
    try {
      // Cancel any active job first
      const job = jobsByBatch.get(batchName);
      if (job && (job.status === 'queued' || job.status === 'running')) {
        try { await cancelJob(job.id); } catch { /* ok */ }
      }
      await deleteBatch(batchName);
      // Also clear from local state
      setClearedBatches((prev) => {
        const next = new Set(prev);
        next.add(batchName);
        return next;
      });
      setSelectedBatches((prev) => {
        const next = new Set(prev);
        next.delete(batchName);
        return next;
      });
      message.success(`已删除批次: ${batchName}`);
      refresh();
    } catch (err) { message.error(describeApiError(err, '删除失败')); }
  }, [jobsByBatch, refresh]);

  // ── bulk actions ──────────────────────────────────────────────────────────
  const handleBulkDelete = useCallback(async () => {
    try {
      let count = 0;
      for (const bn of selectedArr) {
        try {
          const job = jobsByBatch.get(bn);
          if (job && (job.status === 'queued' || job.status === 'running')) {
            try { await cancelJob(job.id); } catch { /* ok */ }
          }
          await deleteBatch(bn);
          setClearedBatches((prev) => { const next = new Set(prev); next.add(bn); return next; });
          count++;
        } catch { /* per-batch failure, continue */ }
      }
      message.success(`已删除 ${count} 个批次`);
      setSelectedBatches(new Set());
      refresh();
    } catch (err) { message.error(describeApiError(err, '批量删除失败')); }
  }, [selectedArr, jobsByBatch, refresh]);

  const handleBulkDownload = useCallback(async () => {
    setBatchDownloadBusy(true);
    setBatchDownloadProgress(null);
    if (selectedArr.length === 1) {
      // Single batch — download directly
      try {
        const batchName = selectedArr[0];
        await downloadBatchZip(batchName, (progress) => {
          setBatchDownloadProgress({ label: `${batchName} 批次下载`, progress });
        });
      } catch (err) { message.error(describeApiError(err, '下载失败')); }
    } else {
      // Multiple batches — download each one sequentially
      let count = 0;
      for (const bn of selectedArr) {
        try {
          await downloadBatchZip(bn, (progress) => {
            setBatchDownloadProgress({ label: `${bn} 批次下载`, progress });
          });
          count++;
        } catch { /* continue */ }
      }
      if (count > 0) message.success(`已下载 ${count} 个批次`);
    }
    setBatchDownloadBusy(false);
  }, [selectedArr]);

  // ── error state ───────────────────────────────────────────────────────────
  if (batchesQ.isError && !batchesQ.data) {
    return (
      <Alert type="error" message="加载批次列表失败"
        description={describeApiError(batchesQ.error, '请检查服务器连接后重试')}
        showIcon action={<Button size="small" onClick={() => batchesQ.refetch()}>重试</Button>} />
    );
  }

  return (
    <>
      {/* ── stats cards ──────────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        {[
          { label: 'DXF 批次', value: batches.length, icon: <FolderOpenOutlined />, color: '#1677ff', bg: '#e6f4ff' },
          { label: '已提取', value: succeededCount, icon: <CheckCircleFilled />, color: '#52c41a', bg: '#f6ffed' },
          { label: '处理中', value: processingCount, icon: <SyncOutlined spin={processingCount > 0} />, color: '#faad14', bg: '#fffbe6' },
          { label: 'DXF 文件总数', value: totalFiles, icon: <CloudOutlined />, color: '#722ed1', bg: '#f9f0ff' },
        ].map((s) => (
          <div key={s.label} style={{ background: s.bg, borderRadius: 10, padding: '14px 18px',
            display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 22, color: s.color, lineHeight: 1 }}>{s.icon}</span>
            <div>
              <div style={{ fontSize: 22, fontWeight: 700, color: '#1f1f1f', lineHeight: 1.2 }}>{s.value}</div>
              <div style={{ fontSize: 13, color: '#8c8c8c', marginTop: 2 }}>{s.label}</div>
            </div>
          </div>
        ))}
      </div>

      <DxfUploadPanel onUploaded={refresh} />

      {/* ── selection toolbar ────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <Space>
          <Checkbox
            checked={allSelected}
            indeterminate={someSelected}
            onChange={toggleSelectAll}
          >
            全选
          </Checkbox>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {batches.length} 个批次
          </Typography.Text>
        </Space>
        {selectedBatches.size > 0 && (
          <Button size="small" onClick={() => setSelectedBatches(new Set())}>
            取消选择
          </Button>
        )}
      </div>

      {/* ── batch action bar ─────────────────────────────────────────────── */}
      {selectedBatches.size > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '8px 16px', marginBottom: 12,
          background: '#e6f4ff', borderRadius: 8,
        }}>
          <Typography.Text strong style={{ marginRight: 8 }}>
            已选 {selectedBatches.size} 个批次
          </Typography.Text>
          <Button type="primary" size="small" icon={<DownloadOutlined />}
            loading={batchDownloadBusy}
            disabled={batchDownloadBusy}
            onClick={handleBulkDownload}>
            打包下载 (.zip)
          </Button>
          <Popconfirm
            title={`确认删除 ${selectedBatches.size} 个批次及其所有文件？`}
            description="此操作不可撤销，批次内所有 .dxf 文件将被删除"
            onConfirm={handleBulkDelete}
            okText="确认删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>删除批次</Button>
          </Popconfirm>
        </div>
      )}
      {batchDownloadProgress && (
        <div style={{ maxWidth: 560, marginBottom: 12 }}>
          <TransferProgressBar
            label={batchDownloadProgress.label}
            progress={batchDownloadProgress.progress}
          />
        </div>
      )}

      {/* ── batch card grid ──────────────────────────────────────────────── */}
      {batches.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '48px 0' }}>
          <FolderOpenOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />
          <p style={{ fontSize: 14, color: '#bfbfbf', marginTop: 12 }}>暂无 DXF 批次</p>
          <p style={{ fontSize: 12, color: '#d9d9d9' }}>
            上传文件夹或压缩包以创建批次
          </p>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
          {batches.map((batch) => (
            <DxfBatchCard
              key={batch.name}
              batch={batch}
              job={jobsByBatch.get(batch.name)}
              selected={selectedBatches.has(batch.name)}
              finalSubmitting={finalSubmittingBatches.has(batch.name)}
              onToggle={toggleBatch}
              onProcess={handleProcess}
              onPreview={handlePreviewExcel}
              onDownload={handleDownloadExcel}
              onRetry={handleRetry}
              onCancel={handleCancel}
              onClear={handleClear}
              onDelete={handleDeleteBatch}
              onProcessExcelFinal={handleProcessExcelFinal}
            />
          ))}
        </div>
      )}

      {/* ── Excel preview modal ──────────────────────────────────────────── */}
      <ExcelPreview
        fileId={previewFileId}
        fileName={previewFileName}
        open={previewFileId !== null}
        onClose={() => { setPreviewFileId(null); setPreviewFileName(''); }}
      />
    </>
  );
}
