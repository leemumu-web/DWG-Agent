import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button,
  Popconfirm,
  Table,
  Typography,
  message,
  Alert,
} from 'antd';
import type { Key } from 'react';
import {
  DeleteOutlined,
  DownloadOutlined,
  InboxOutlined,
} from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listFiles,
  listFilesPage,
  listBatches,
  downloadFile,
  bulkDeleteFiles,
  bulkDeleteBatches,
} from '../files';
import {
  cancelJobs,
  createConversionBatches,
  getJobResults,
  listJobsForFiles,
  retryJob,
} from '../jobs';
import { useConversionEvents } from './hooks/useConversionEvents';
import { DxfPreviewModal, ZipDownloadModal } from '../files';
import type { StoredFile } from '../files';
import type { ConversionBatchSubmission, Job } from '../jobs';
import { ConversionFoldersPanel } from './components/conversion/ConversionFoldersPanel';
import {
  ConversionUploadPanel,
  type ConversionOperation,
} from './components/conversion/ConversionUploadPanel';
import {
  buildConversionColumns,
} from './components/conversion/conversionColumns';
import { ConversionOverview } from './components/conversion/ConversionOverview';

// ── helpers ──────────────────────────────────────────────────────────────────

const ACTIVE_JOB_STATUSES = new Set([
  'pending',
  'queued',
  'running',
  'validating',
  'waiting_cad_worker',
]);

function isStuckJob(job: Job, now = Date.now()): boolean {
  return job.status === 'queued'
    && job.progress === 0
    && now - new Date(job.created_at).getTime() > 60_000;
}

function actionableFiles(files: StoredFile[], jobsByFileId: Map<number, Job>): StoredFile[] {
  const now = Date.now();
  return files.filter((file) => {
    const job = jobsByFileId.get(file.id);
    return !job
      || job.status === 'failed'
      || job.status === 'cancelled'
      || isStuckJob(job, now);
  });
}

function reportSubmission(prefix: string, submission: ConversionBatchSubmission): void {
  if (submission.unsubmittedFileIds.length > 0) {
    message.warning(
      `${prefix}；已提交 ${submission.submittedFileIds.length} 个，待补交 ${submission.unsubmittedFileIds.length} 个`,
    );
    return;
  }
  message.success(`${prefix}；已提交 ${submission.submittedJobs.length} 个转换任务`);
}

// ── page ─────────────────────────────────────────────────────────────────────

export interface ConversionPageProps {
  fileExt: string;
  resultExt: string;
  taskType: string;
  resultType: string;
  title: string;
  tagPending: string;
  tagDone: string;
  downloadResultLabel: string;
  uploadHint: string;
  acceptExt: string;
  emptyText: string;
}

export function ConversionPage(props: ConversionPageProps) {
  const p = props;
  const sourceFormat = p.fileExt.slice(1) as 'dwg' | 'dxf';
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);
  const [selectedBatchNames, setSelectedBatchNames] = useState<string[]>([]);
  const [zipModalOpen, setZipModalOpen] = useState(false);
  const [batchZipModalOpen, setBatchZipModalOpen] = useState(false);
  const [pauseLoading, setPauseLoading] = useState(false);
  const [operation, setOperation] = useState<ConversionOperation>(null);
  const [tick, setTick] = useState(0);
  const [previewFileId, setPreviewFileId] = useState<number | null>(null);
  const [previewFileName, setPreviewFileName] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const queryClient = useQueryClient();

  // ── data ──────────────────────────────────────────────────────────────────
  // Each pipeline requests only its own data: files filtered by fileExt,
  // jobs filtered by taskType, batches filtered by fileExt.
  const filesQ = useQuery({
    queryKey: ['files', p.fileExt, selectedBatch, page, pageSize],
    queryFn: () => listFilesPage({
      page,
      page_size: pageSize,
      batch_name: selectedBatch || undefined,
      file_ext: p.fileExt,
    }),
    staleTime: 2000,
  });
  const batchesQ = useQuery({
    queryKey: ['batches', p.fileExt],
    queryFn: ({ queryKey }) => listBatches(queryKey[1] as string),
    staleTime: 5000,
    enabled: selectedBatch === null,
  });
  const scopeFilesQ = useQuery({
    queryKey: ['conversion-scope-files', p.fileExt, selectedBatch],
    queryFn: () => listFiles(selectedBatch || undefined, p.fileExt),
    staleTime: 2000,
  });
  const scopeFiles = scopeFilesQ.data ?? [];
  const scopeFileIds = useMemo(() => scopeFiles.map((file) => file.id), [scopeFiles]);
  const scopeFileIdsKey = scopeFileIds.join(',');
  const scopeJobsKey = useMemo(
    () => ['conversion-jobs', p.taskType, scopeFileIdsKey] as const,
    [p.taskType, scopeFileIdsKey],
  );
  const jobsQ = useQuery({
    queryKey: scopeJobsKey,
    queryFn: () => listJobsForFiles(p.taskType, scopeFileIds),
    staleTime: 2000,
    enabled: scopeFileIds.length > 0,
  });

  const allFiles = filesQ.data?.data ?? [];

  const dwgFiles = useMemo(
    () => allFiles.filter((f) => f.file_ext === p.fileExt),
    [allFiles],
  );

  // Always show DWG rows only — DXF info is embedded in each row
  // via the conversion status column and DXF download button.
  // This cuts the table size in half.
  const tableFiles = dwgFiles;

  const hasActive = useMemo(
    () => (jobsQ.data ?? []).some(
      (job) => ACTIVE_JOB_STATUSES.has(job.status) && !isStuckJob(job),
    ),
    // tick keeps stuck-queue classification current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [jobsQ.data, tick],
  );

  // SSE is primary; a slow fallback poll repairs state after network/auth interruptions.
  useEffect(() => {
    if (!hasActive) return;
    const id = setInterval(() => { filesQ.refetch(); scopeFilesQ.refetch(); jobsQ.refetch(); }, 10_000);
    return () => clearInterval(id);
  }, [hasActive, filesQ, scopeFilesQ, jobsQ]);

  const streamFileIds = useMemo(
    () => (jobsQ.data ?? []).flatMap((job) => {
      const fileId = (job.params_json as Record<string, unknown> | null)?.file_id;
      return typeof fileId === 'number' ? [fileId] : [];
    }),
    [jobsQ.data],
  );
  const mergeStreamJobs = useCallback((patches: Array<Partial<Job> & { id: number }>) => {
    queryClient.setQueryData<Job[]>(scopeJobsKey, (current) => {
      if (!current) return current;
      const patchesById = new Map(patches.map((patch) => [patch.id, patch]));
      return current.map((job) => {
        const patch = patchesById.get(job.id);
        return patch ? { ...job, ...patch } : job;
      });
    });
  }, [queryClient, scopeJobsKey]);
  useConversionEvents(p.taskType, streamFileIds, mergeStreamJobs);

  // file_id → latest Job
  const jobsByFileId = useMemo(() => {
    const map = new Map<number, Job>();
    for (const j of jobsQ.data ?? []) {
      const fid = (j.params_json as Record<string, unknown> | null)?.file_id as number | undefined;
      if (fid && !map.has(fid)) map.set(fid, j);
    }
    return map;
  }, [jobsQ.data]);

  // Periodic clock tick so stuck-queued detection stays fresh.
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 15_000);
    return () => clearInterval(id);
  }, []);

  const pendingFiles = useMemo(
    () => actionableFiles(scopeFiles, jobsByFileId),
    // tick keeps stuck-queue classification current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scopeFiles, jobsByFileId, tick],
  );

  const refresh = useCallback(() => {
    filesQ.refetch();
    scopeFilesQ.refetch();
    jobsQ.refetch();
    batchesQ.refetch();
  }, [filesQ, scopeFilesQ, jobsQ, batchesQ]);

  // ── single file actions ───────────────────────────────────────────────────
  const handleDownload = useCallback(async (file: StoredFile) => {
    try { await downloadFile(file.id, file.original_name); } catch (err) { message.error(err instanceof Error ? err.message : '下载失败'); }
  }, []);

  const handleDownloadResult = useCallback(async (job: Job, sourceName: string) => {
    try {
      const results = await getJobResults(job.id);
      const result = results.find((r) => r.result_type === p.resultType);
      if (!result?.result_file_id) { message.error(`${p.tagDone} 结果未找到`); return; }
      await downloadFile(result.result_file_id, sourceName.replace(new RegExp('\\' + p.fileExt + '$', 'i'), p.resultExt));
    } catch (err) { message.error(err instanceof Error ? err.message : `获取 ${p.tagDone} 失败`); }
  }, []);

  const handlePreviewResult = useCallback(async (job: Job, sourceName: string) => {
    try {
      const results = await getJobResults(job.id);
      const result = results.find((item) => item.result_type === p.resultType);
      if (!result?.result_file_id) {
        message.error(`${p.tagDone} 结果未找到`);
        return;
      }
      setPreviewFileId(result.result_file_id);
      setPreviewFileName(
        sourceName.replace(new RegExp(`\\${p.fileExt}$`, 'i'), p.resultExt),
      );
    } catch (error) {
      message.error(error instanceof Error ? error.message : '获取 DXF 预览失败');
    }
  }, [p.fileExt, p.resultExt, p.resultType, p.tagDone]);

  const handleRetry = useCallback(async (jobId: number) => {
    try { await retryJob(jobId); message.success('已重新提交'); refresh(); } catch (err) { message.error(err instanceof Error ? err.message : '重试失败'); }
  }, [refresh]);

  // ── bulk actions ──────────────────────────────────────────────────────────
  const handleBulkDelete = useCallback(async () => {
    try { await bulkDeleteFiles(selectedRowKeys); message.success(`已删除 ${selectedRowKeys.length} 个文件`); setSelectedRowKeys([]); refresh(); }
    catch (err) { message.error(err instanceof Error ? err.message : '批量删除失败'); }
  }, [selectedRowKeys, refresh]);

  const handlePauseAll = useCallback(async () => {
    setPauseLoading(true);
    try {
      const activeJobIds = (jobsQ.data ?? [])
        .filter((job) => ['pending', 'queued', 'running', 'validating', 'waiting_cad_worker'].includes(job.status))
        .map((job) => job.id);
      const res = await cancelJobs(activeJobIds);
      message.success(`已暂停 ${res.cancelled_count} 个任务`);
      refresh();
    } catch (err) { message.error(err instanceof Error ? err.message : '暂停失败'); }
    setPauseLoading(false);
  }, [jobsQ.data, refresh]);

  const handleResumeAll = useCallback(async () => {
    setPauseLoading(true);
    try {
      const targets = [...pendingFiles];
      if (targets.length === 0) {
        message.info('没有需要转换的文件');
        refresh();
        setPauseLoading(false);
        return;
      }
      const submission = await createConversionBatches(p.taskType, targets.map((file) => file.id));
      reportSubmission('提交完成', submission);
      refresh();
    } catch (err) { message.error(err instanceof Error ? err.message : '提交失败'); }
    setPauseLoading(false);
  }, [p.taskType, pendingFiles, refresh]);

  // ── batch-level actions ──────────────────────────────────────────────────
  const toggleBatchSelection = (name: string) => {
    if (operation) return;
    setSelectedBatchNames((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
    setSelectedRowKeys([]);
  };

  const handleBatchDownload = useCallback(async () => {
    if (selectedBatchNames.length === 0 || operation) return;
    setOperation('batch-package');
    try {
      const groups = await Promise.all(
        selectedBatchNames.map((batchName) => listFiles(batchName, p.fileExt)),
      );
      const allIds = [...new Set(groups.flat().map((file) => file.id))];
      if (allIds.length === 0) {
        message.warning('所选文件夹中没有可打包的源文件');
        return;
      }
      setBatchZipFileIds(allIds);
      setBatchZipModalOpen(true);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '收集打包文件失败，请重试');
    } finally {
      setOperation(null);
    }
  }, [operation, p.fileExt, selectedBatchNames]);

  const handleBatchDelete = useCallback(async () => {
    if (selectedBatchNames.length === 0 || operation) return;
    setOperation('batch-delete');
    try {
      const result = await bulkDeleteBatches(selectedBatchNames);
      message.success(
        `已删除 ${result.deleted_batch_count} 个文件夹、${result.deleted_file_count} 个文件，并取消 ${result.cancelled_job_count} 个任务`,
      );
      setSelectedBatchNames([]);
      refresh();
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败，已保留选择，请重试');
      refresh();
    } finally {
      setOperation(null);
    }
  }, [operation, selectedBatchNames, refresh]);

  // ── batch zip file IDs ───────────────────────────────────────────────────
  const [batchZipFileIds, setBatchZipFileIds] = useState<number[]>([]);

  // ── stats ─────────────────────────────────────────────────────────────────
  const statusLoading = scopeFilesQ.isLoading
    || (scopeFileIds.length > 0 && jobsQ.isLoading);
  const statusLoadFailed = scopeFilesQ.isError || jobsQ.isError;
  const summary = useMemo(() => {
    let succeeded = 0;
    let failed = 0;
    let processing = 0;
    let progressPoints = 0;
    for (const file of scopeFiles) {
      const job = jobsByFileId.get(file.id);
      if (!job || isStuckJob(job)) continue;
      if (job.status === 'succeeded') {
        succeeded += 1;
        progressPoints += 100;
      } else if (ACTIVE_JOB_STATUSES.has(job.status)) {
        processing += 1;
        progressPoints += Math.max(0, Math.min(100, job.progress));
      } else if (job.status === 'failed' || job.status === 'cancelled') {
        failed += 1;
      }
    }
    return {
      succeeded,
      failed,
      processing,
      progress: scopeFiles.length > 0
        ? Math.round(progressPoints / scopeFiles.length)
        : 0,
    };
    // tick keeps stuck-queue classification current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeFiles, jobsByFileId, tick]);
  const { succeeded, failed, processing } = summary;
  const totalSize = scopeFiles.reduce((s, f) => s + f.size_bytes, 0);
  const aggregateProgress = summary.progress;
  const isFirstLoad = filesQ.isLoading;
  const batches = batchesQ.data ?? [];
  const selectedBatchSourceCount = batches
    .filter((batch) => selectedBatchNames.includes(batch.name))
    .reduce((total, batch) => total + batch.file_count, 0);
  const selectedBatchPreview = selectedBatchNames.slice(0, 3).join('、');
  const selectedBatchRemainder = Math.max(0, selectedBatchNames.length - 3);

  // ── table row selection (clears batch selection when files are selected) ──
  const rowSelection = useMemo(() => ({
    selectedRowKeys,
    onChange: (keys: Key[]) => {
      setSelectedRowKeys(keys.map(Number) as number[]);
      if (keys.length > 0) setSelectedBatchNames([]);
    },
    getCheckboxProps: () => ({ style: { margin: 0 } }),
    preserveSelectedRowKeys: true,
  }), [selectedRowKeys]);

  // ── error state ───────────────────────────────────────────────────────────
  if (filesQ.isError && !filesQ.data) {
    return (
      <Alert type="error" message="加载文件列表失败"
        description={(filesQ.error as Error)?.message || '请检查后端服务是否正常运行'}
        showIcon action={<Button size="small" onClick={() => filesQ.refetch()}>重试</Button>} />
    );
  }

  // ── file table columns ────────────────────────────────────────────────────
  const columns = buildConversionColumns({
    tagPending: p.tagPending,
    tagDone: p.tagDone,
    resultExt: p.resultExt,
    downloadResultLabel: p.downloadResultLabel,
    statusLoading,
    statusLoadFailed,
    jobsByFileId,
    onPreviewSource: (file) => {
      setPreviewFileId(file.id);
      setPreviewFileName(file.original_name);
    },
    onDownloadSource: handleDownload,
    onPreviewResult: handlePreviewResult,
    onDownloadResult: handleDownloadResult,
    onRetry: handleRetry,
  });

  return (
    <>
      <ConversionOverview
        title={p.title}
        tagDone={p.tagDone}
        selectedBatch={selectedBatch}
        total={filesQ.data?.pagination.total ?? 0}
        scopeCount={scopeFiles.length}
        totalSize={totalSize}
        succeeded={succeeded}
        failed={failed}
        processing={processing}
        pendingCount={pendingFiles.length}
        aggregateProgress={aggregateProgress}
        statusLoading={statusLoading}
        statusLoadFailed={statusLoadFailed}
        hasActive={hasActive}
        actionLoading={pauseLoading}
        onBack={() => { setSelectedBatch(null); setPage(1); setSelectedRowKeys([]); }}
        onPauseAll={handlePauseAll}
        onResumeAll={handleResumeAll}
        onRefresh={refresh}
      />

      {/* ── batch/folder view (top level only) ──────────────────────── */}
      {selectedBatch === null && batches.length > 0 && (
        <ConversionFoldersPanel
          batches={batches}
          selectedBatchNames={selectedBatchNames}
          operation={operation}
          selectedSourceCount={selectedBatchSourceCount}
          selectedPreview={selectedBatchPreview}
          selectedRemainder={selectedBatchRemainder}
          onSelectionChange={setSelectedBatchNames}
          onClearFileSelection={() => setSelectedRowKeys([])}
          onToggle={toggleBatchSelection}
          onOpen={(name) => { setSelectedBatch(name); setPage(1); setSelectedRowKeys([]); }}
          onDownload={handleBatchDownload}
          onDelete={handleBatchDelete}
        />
      )}

      {/* ── batch action bar ─────────────────────────────────────────── */}

      {/* ── upload area ──────────────────────────────────────────────── */}
      <ConversionUploadPanel
        selectedBatch={selectedBatch}
        acceptExt={p.acceptExt}
        fileExt={p.fileExt}
        tagPending={p.tagPending}
        uploadHint={p.uploadHint}
        taskType={p.taskType}
        operation={operation}
        onOperationChange={setOperation}
        onUploaded={refresh}
      />

      {/* ── bulk action bar ──────────────────────────────────────────── */}
      {selectedRowKeys.length > 0 && (
        <div className="selection-bar">
          <Typography.Text strong style={{ marginRight: 8 }}>
            已选 {selectedRowKeys.length} 个
          </Typography.Text>
          <Button type="primary" size="small" icon={<DownloadOutlined />}
            onClick={() => setZipModalOpen(true)}>
            打包下载 (.zip)
          </Button>
          <Popconfirm title={`确认删除 ${selectedRowKeys.length} 个文件？`}
            onConfirm={handleBulkDelete} okText="确认删除" cancelText="取消" okButtonProps={{ danger: true }}>
            <Button size="small" danger icon={<DeleteOutlined />}>删除选中</Button>
          </Popconfirm>
          <Button size="small" onClick={() => setSelectedRowKeys([])}>取消</Button>
        </div>
      )}

      {/* ── file table ───────────────────────────────────────────────── */}
      <Table
        className="conversion-table"
        rowKey="id"
        dataSource={tableFiles}
        columns={columns}
        rowSelection={rowSelection}
        loading={isFirstLoad}
        size="middle"
        pagination={{
          current: page,
          pageSize,
          total: filesQ.data?.pagination.total ?? 0,
          pageSizeOptions: [10, 15, 20, 30, 50, 100],
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (t, range) => `${range[0]}-${range[1]} / 共 ${t} 个`,
          onChange: (nextPage, nextPageSize) => {
            setPage(nextPageSize === pageSize ? nextPage : 1);
            setPageSize(nextPageSize);
            setSelectedRowKeys([]);
          },
        }}
        locale={{
          emptyText: (
            <div style={{ padding: '48px 0' }}>
              <InboxOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />
              <p style={{ fontSize: 14, color: '#bfbfbf', marginTop: 12 }}>{p.emptyText}</p>
              <p style={{ fontSize: 12, color: '#d9d9d9' }}>
                {selectedBatch ? `拖拽 ${p.acceptExt} 到上方区域添加文件` : `上传文件夹或拖拽 ${p.acceptExt} 文件到上方区域`}
              </p>
            </div>
          ),
        }}
        scroll={{ x: 860 }}
      />

      <ZipDownloadModal
        open={zipModalOpen}
        fileIds={selectedRowKeys}
        fileCount={selectedRowKeys.length}
        sourceFormat={sourceFormat}
        onClose={() => setZipModalOpen(false)}
        onDone={() => { setSelectedRowKeys([]); refresh(); }}
      />
      <ZipDownloadModal
        open={batchZipModalOpen}
        fileIds={batchZipFileIds}
        fileCount={batchZipFileIds.length}
        sourceFormat={sourceFormat}
        onClose={() => { setBatchZipModalOpen(false); setSelectedBatchNames([]); }}
        onDone={() => { setBatchZipModalOpen(false); setSelectedBatchNames([]); refresh(); }}
      />
      <DxfPreviewModal
        fileId={previewFileId}
        fileName={previewFileName}
        open={previewFileId !== null}
        onClose={() => {
          setPreviewFileId(null);
          setPreviewFileName('');
        }}
      />
    </>
  );
}
