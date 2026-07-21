import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FileUpload } from '../files';
import {
  Breadcrumb,
  Button,
  Card,
  Checkbox,
  Popconfirm,
  Progress,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
  Alert,
} from 'antd';
import type { Key } from 'react';
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
  FileOutlined,
  FileTextOutlined,
  CheckCircleFilled,
  SyncOutlined,
  CloseCircleFilled,
  EyeOutlined,
  InboxOutlined,
  CloudOutlined,
  FileZipOutlined,
} from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listFiles,
  listFilesPage,
  listBatches,
  downloadFile,
  bulkDeleteFiles,
  bulkDeleteBatches,
  uploadFolder,
  uploadFile,
  uploadZip,
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
import type { BatchInfo, StoredFile } from '../files';
import type { ConversionBatchSubmission, Job } from '../jobs';

// ── helpers ──────────────────────────────────────────────────────────────────

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const STATUS: Record<string, { color: string; bg: string; label: string; icon: React.ReactNode }> = {
  succeeded: { color: '#52c41a', bg: '#f6ffed', label: '已完成', icon: <CheckCircleFilled style={{ color: '#52c41a' }} /> },
  pending:   { color: '#faad14', bg: '#fffbe6', label: '待排队', icon: <SyncOutlined style={{ color: '#faad14' }} /> },
  running:   { color: '#1677ff', bg: '#e6f4ff', label: '转换中', icon: <SyncOutlined style={{ color: '#1677ff' }} spin /> },
  queued:    { color: '#faad14', bg: '#fffbe6', label: '排队中', icon: <SyncOutlined style={{ color: '#faad14' }} /> },
  validating:{ color: '#1677ff', bg: '#e6f4ff', label: '校验中', icon: <SyncOutlined style={{ color: '#1677ff' }} spin /> },
  waiting_cad_worker: { color: '#1677ff', bg: '#e6f4ff', label: '等待 CAD', icon: <SyncOutlined style={{ color: '#1677ff' }} spin /> },
  failed:    { color: '#ff4d4f', bg: '#fff2f0', label: '失败',   icon: <CloseCircleFilled style={{ color: '#ff4d4f' }} /> },
  cancelled: { color: '#8c8c8c', bg: '#fafafa', label: '已取消', icon: <CloseCircleFilled style={{ color: '#8c8c8c' }} /> },
};

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
  const [uploadProgress, setUploadProgress] = useState<{ processed: number; total: number } | null>(null);
  const [operation, setOperation] = useState<'file-upload' | 'folder-upload' | 'zip-upload' | 'batch-package' | 'batch-delete' | null>(null);
  const [tick, setTick] = useState(0);
  const [previewFileId, setPreviewFileId] = useState<number | null>(null);
  const [previewFileName, setPreviewFileName] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
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

  // ── folder upload ────────────────────────────────────────────────────────
  const handleFolderClick = () => { if (!operation) folderInputRef.current?.click(); };

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
  const columns = [
    {
      title: '文件名', dataIndex: 'original_name',
      render: (name: string, record: StoredFile) => {
        return (
          <Space>
            <Tag
              style={{ margin: 0, borderRadius: 4, fontSize: 11, lineHeight: '18px', padding: '0 6px' }}
              color="processing"
            >
              {p.tagPending}
            </Tag>
            <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 32, height: 32, borderRadius: 8, background: '#f5f5f5' }}>
              <FileOutlined style={{ color: '#8c8c8c', fontSize: 15 }} />
            </span>
            <Tooltip title={name}><Typography.Text style={{ maxWidth: 400 }} ellipsis>{name}</Typography.Text></Tooltip>
          </Space>
        );
      },
    },
    {
      title: '大小', dataIndex: 'size_bytes', width: 90, align: 'right' as const,
      render: (v: number) => <Typography.Text type="secondary">{fmtSize(v)}</Typography.Text>,
    },
    {
      title: '转换状态', width: 280,
      render: (_: unknown, record: StoredFile) => {
        if (statusLoading) {
          return <Typography.Text type="secondary">正在加载状态</Typography.Text>;
        }
        if (statusLoadFailed) {
          return <Typography.Text type="danger">状态加载失败，请刷新重试</Typography.Text>;
        }
        const job = jobsByFileId.get(record.id);
        if (!job) return <Typography.Text type="secondary">未提交</Typography.Text>;
        const s = STATUS[job.status] ?? STATUS.cancelled;
        return (
          <Space size={8}>
            <Tooltip
              title={job.status === 'failed'
                ? `${job.error_code || '转换失败'}；可使用“重新提交”再次处理`
                : undefined}
            >
              <Tag style={{ color: s.color, background: s.bg, border: 'none', borderRadius: 6 }}>
                {s.icon} <span style={{ marginLeft: 4 }}>{s.label}</span>
              </Tag>
            </Tooltip>
            <Progress percent={job.progress} size="small" style={{ width: 120, margin: 0 }}
              strokeColor={s.color}
              status={job.status === 'failed' ? 'exception' : job.status === 'succeeded' ? 'success' : undefined} />
          </Space>
        );
      },
    },
    {
      title: '上传时间', dataIndex: 'created_at', width: 110,
      render: (v: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {new Date(v).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
        </Typography.Text>
      ),
    },
    {
      title: '操作', width: 140, align: 'center' as const,
      render: (_: unknown, record: StoredFile) => {
        const job = jobsByFileId.get(record.id);
        const isSucceeded = job?.status === 'succeeded';
        const isFailed = job?.status === 'failed' || job?.status === 'cancelled';
        return (
          <Space size={2}>
            {record.file_ext === '.dxf' && (
              <Tooltip title="预览 DXF">
                <Button
                  aria-label="预览 DXF"
                  type="text"
                  size="small"
                  icon={<EyeOutlined style={{ color: '#0891b2' }} />}
                  onClick={() => {
                    setPreviewFileId(record.id);
                    setPreviewFileName(record.original_name);
                  }}
                />
              </Tooltip>
            )}
            <Tooltip title={`下载 ${p.tagPending}`}>
              <Button aria-label={`下载 ${p.tagPending}`} type="text" size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(record)} />
            </Tooltip>
            {isSucceeded && job && (
              <>
                {p.resultExt === '.dxf' && (
                  <Tooltip title="预览 DXF">
                    <Button
                      aria-label="预览 DXF"
                      type="text"
                      size="small"
                      icon={<EyeOutlined style={{ color: '#2563eb' }} />}
                      onClick={() => handlePreviewResult(job, record.original_name)}
                    />
                  </Tooltip>
                )}
                <Tooltip title={p.downloadResultLabel}>
                  <Button aria-label={p.downloadResultLabel} type="text" size="small" icon={<FileTextOutlined style={{ color: '#1677ff' }} />}
                    onClick={() => handleDownloadResult(job, record.original_name)} />
                </Tooltip>
              </>
            )}
            {isFailed && job && (
              <Tooltip title="重新提交转换任务">
                <Button aria-label="重新提交" type="text" size="small" danger icon={<ReloadOutlined />} onClick={() => handleRetry(job.id)} />
              </Tooltip>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <>
      {/* ── header ────────────────────────────────────────────────────── */}
      <div className="conversion-header">
        <div>
          {selectedBatch ? (
            <Space size={4}>
              <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => { setSelectedBatch(null); setPage(1); setSelectedRowKeys([]); }}>
                返回
              </Button>
              <Breadcrumb items={[
                { title: <a onClick={() => { setSelectedBatch(null); setPage(1); setSelectedRowKeys([]); }}>全部文件</a> },
                { title: <Space><FolderOutlined />{selectedBatch}</Space> },
              ]} />
            </Space>
          ) : null}
        </div>
        <Space>
          {hasActive && (
            <Button icon={<PauseCircleOutlined />} loading={pauseLoading} onClick={handlePauseAll}>
              全部暂停
            </Button>
          )}
          {!statusLoading && !statusLoadFailed && pendingFiles.length > 0 && (
            <Button type="primary" icon={<SyncOutlined />} loading={pauseLoading} onClick={handleResumeAll}>
              提交/重试 {pendingFiles.length} 个
            </Button>
          )}
        </Space>
      </div>

      {/* ── master progress ────────────────────────────────────────────── */}
      {(statusLoading || statusLoadFailed || scopeFiles.length > 0) && (
        <div className="conversion-progress">
          <SyncOutlined spin={statusLoading || processing > 0} style={{ fontSize: 20, color: statusLoadFailed ? '#ff4d4f' : processing > 0 ? '#1677ff' : '#52c41a' }} />
          {statusLoading ? (
            <Typography.Text type="secondary">正在加载转换状态…</Typography.Text>
          ) : statusLoadFailed ? (
            <Space style={{ flex: 1, justifyContent: 'space-between' }}>
              <Typography.Text type="danger">转换状态加载失败，当前统计可能不完整</Typography.Text>
              <Button size="small" onClick={refresh}>重新加载</Button>
            </Space>
          ) : (
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, gap: 12, flexWrap: 'wrap' }}>
                <Typography.Text strong>{selectedBatch ? `文件夹“${selectedBatch}”` : '全部文件'}成功进度</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  成功 {succeeded} / {scopeFiles.length} · 失败 {failed} · 处理中 {processing} · 待提交/重试 {pendingFiles.length} · {aggregateProgress}%
                </Typography.Text>
              </div>
              <Progress percent={aggregateProgress} strokeColor={{ '0%': '#1677ff', '100%': '#52c41a' }} size={8} showInfo={false} />
            </div>
          )}
        </div>
      )}

      {/* ── stats ─────────────────────────────────────────────────────── */}
      <div className="conversion-stats">
        {[
          { label: `${p.title}总数`, value: filesQ.data?.pagination.total ?? 0, icon: <FileOutlined />, color: '#2563eb', bg: '#eff6ff' },
          { label: `范围内已转换 ${p.tagDone}`, value: statusLoading ? '—' : succeeded, icon: <CheckCircleFilled />, color: '#059669', bg: '#ecfdf5' },
          { label: '范围内处理中', value: statusLoading ? '—' : processing, icon: <SyncOutlined spin={processing > 0} />, color: '#d97706', bg: '#fffbeb' },
          { label: '范围内存储量', value: statusLoading ? '—' : fmtSize(totalSize), icon: <CloudOutlined />, color: '#7c3aed', bg: '#f5f3ff' },
        ].map((s) => (
          <div key={s.label} className="conversion-stat">
            <span className="conversion-stat-icon" style={{ color: s.color, background: s.bg }}>{s.icon}</span>
            <div style={{ minWidth: 0 }}>
              <div className="conversion-stat-value">{s.value}</div>
              <div className="conversion-stat-label">{s.label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* ── batch/folder view (top level only) ──────────────────────── */}
      {selectedBatch === null && batches.length > 0 && (
        <div className="folder-section">
          <div className="folder-heading">
            <Typography.Text strong style={{ fontSize: 14 }}>
              <FolderOpenOutlined style={{ marginRight: 6 }} />文件夹
              <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                （上传文件夹时自动创建，勾选后可打包下载或删除）
              </Typography.Text>
            </Typography.Text>
          </div>
          <div className="folder-actions" aria-label="文件夹批量操作">
            <Typography.Text strong>
              {selectedBatchNames.length > 0 ? `已选 ${selectedBatchNames.length} 个文件夹` : `共 ${batches.length} 个文件夹`}
            </Typography.Text>
            {selectedBatchNames.length < batches.length && (
              <Button size="small" disabled={operation !== null}
                onClick={() => { setSelectedBatchNames(batches.map((batch) => batch.name)); setSelectedRowKeys([]); }}>
                全选 {batches.length} 个文件夹
              </Button>
            )}
            {selectedBatchNames.length > 0 && (
              <>
                <Button size="small" disabled={operation !== null} onClick={() => setSelectedBatchNames([])}>清除选择</Button>
                <Button type="primary" size="small" icon={<DownloadOutlined />}
                  loading={operation === 'batch-package'} disabled={operation !== null && operation !== 'batch-package'}
                  onClick={handleBatchDownload}>
                  打包下载 {selectedBatchNames.length} 个文件夹
                </Button>
                <Popconfirm
                  title={`确认完整删除 ${selectedBatchNames.length} 个文件夹？`}
                  description={(
                    <div className="folder-delete-summary">
                      <div>将删除已知 {selectedBatchSourceCount} 个源文件、它们的生成结果，并取消相关活动任务。</div>
                      <div>文件夹：{selectedBatchPreview}{selectedBatchRemainder > 0 ? ` 等 ${selectedBatchNames.length} 个` : ''}</div>
                      <div>删除为整体事务：全部成功或全部保留。</div>
                    </div>
                  )}
                  onConfirm={handleBatchDelete} okText="确认删除" cancelText="取消"
                  okButtonProps={{ danger: true, loading: operation === 'batch-delete' }} disabled={operation !== null}
                >
                  <Button size="small" danger icon={<DeleteOutlined />} loading={operation === 'batch-delete'} disabled={operation !== null}>
                    删除 {selectedBatchNames.length} 个文件夹
                  </Button>
                </Popconfirm>
              </>
            )}
          </div>
          <div className="folder-grid">
            {batches.map((b: BatchInfo) => {
              const isChecked = selectedBatchNames.includes(b.name);
              return (
                <Card
                  key={b.name}
                  hoverable
                  size="small"
                  className={`folder-card${isChecked ? ' folder-card-selected' : ''}`}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                    <Checkbox
                      aria-label={`选择文件夹 ${b.name}`}
                      checked={isChecked}
                      disabled={operation !== null}
                      onChange={() => toggleBatchSelection(b.name)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <button type="button" className="folder-open-button"
                      aria-label={`打开文件夹 ${b.name}`} disabled={operation !== null}
                      onClick={() => { setSelectedBatch(b.name); setPage(1); setSelectedRowKeys([]); }}
                    >
                      <Card.Meta
                        avatar={<FolderOutlined style={{ fontSize: 24, color: '#faad14' }} />}
                        title={<Typography.Text ellipsis style={{ maxWidth: 120 }}>{b.name}</Typography.Text>}
                        description={
                          <div>
                            <div>{b.file_count} 个文件</div>
                            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                              {new Date(b.latest_created_at).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                            </Typography.Text>
                          </div>
                        }
                      />
                    </button>
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* ── batch action bar ─────────────────────────────────────────── */}

      {/* ── upload area ──────────────────────────────────────────────── */}
      <div className="upload-toolbar">
        <input
          ref={folderInputRef}
          type="file"
          /* @ts-expect-error webkitdirectory */
          webkitdirectory=""
          multiple
          disabled={operation !== null}
          style={{ display: 'none' }}
          onChange={async (e) => {
            const raw = e.target.files;
            if (raw && raw.length > 0) {
              setOperation('folder-upload');
              const files = Array.from(raw);
              const firstPath = (files[0] as { webkitRelativePath?: string }).webkitRelativePath || '';
              const folderName = selectedBatch || firstPath.split('/')[0] || `导入_${Date.now()}`;
              const matchedCount = files.filter((file) => file.name.toLowerCase().endsWith(p.acceptExt)).length;
              setUploadProgress({ processed: 0, total: matchedCount });
              try {
                const result = await uploadFolder(files, folderName, {
                  fileExt: p.acceptExt,
                  concurrency: 4,
                  onFile: (file: File, bn: string) => uploadFile(file, bn),
                  onProgress: (processed, total) => setUploadProgress({ processed, total }),
                });
                const uploaded = result.results as StoredFile[];
                if (uploaded.length > 0) {
                  const submission = await createConversionBatches(p.taskType, uploaded.map((file) => file.id));
                  reportSubmission(`已上传 ${result.success}/${result.total} 个文件`, submission);
                  if (result.failures.length > 0) {
                    const examples = result.failures.slice(0, 3)
                      .map((failure) => `${failure.file_name}: ${failure.reason}`)
                      .join('；');
                    const remaining = result.failures.length > 3 ? `；另有 ${result.failures.length - 3} 个失败` : '';
                    message.warning(`部分文件上传失败：${examples}${remaining}`, 10);
                  }
                  refresh();
                } else if (result.total > 0) {
                  const examples = result.failures.slice(0, 3)
                    .map((failure) => `${failure.file_name}: ${failure.reason}`)
                    .join('；');
                  message.error(`全部 ${result.total} 个文件上传失败${examples ? `：${examples}` : ''}`, 10);
                } else {
                  message.warning(`文件夹中没有 ${p.fileExt} 文件`);
                }
              } catch (err) {
                message.error(err instanceof Error ? err.message : '文件夹导入失败');
              } finally {
                setUploadProgress(null);
                setOperation(null);
              }
              e.target.value = '';
            }
          }}
        />
        <FileUpload
          onUploaded={refresh}
          batchName={selectedBatch ?? undefined}
          acceptExt={p.acceptExt}
          label={`上传 ${p.tagPending} 文件`}
          disabled={operation !== null}
          onBusyChange={(busy) => setOperation(busy ? 'file-upload' : null)}
          uploadFn={async (file: File, bn?: string) => {
            const stored = await uploadFile(file, bn);
            const submission = await createConversionBatches(p.taskType, [stored.id]);
            if (submission.unsubmittedFileIds.length > 0) {
              throw new Error('文件已上传，但转换任务未提交；请使用“提交/重试”补交');
            }
            return submission;
          }}
        />
        <Button icon={<FolderOpenOutlined />} onClick={handleFolderClick}
          loading={operation === 'folder-upload'} disabled={operation !== null}
          style={{ borderColor: '#722ed1', color: '#722ed1', fontWeight: 500 }}>
          上传文件夹
        </Button>
        <input
          ref={zipInputRef}
          type="file"
          accept=".zip"
          disabled={operation !== null}
          style={{ display: 'none' }}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            setOperation('zip-upload');
            try {
              const result = await uploadZip(file, p.acceptExt);
              if (result.success_count > 0) {
                const submission = await createConversionBatches(
                  p.taskType,
                  result.files.map((stored) => stored.id),
                );
                reportSubmission(
                  `已从压缩包上传 ${result.success_count}/${result.success_count + result.skipped_count} 个文件`,
                  submission,
                );
                refresh();
              } else {
                message.warning(`压缩包中没有 ${p.acceptExt} 文件`);
              }
            } catch (err) {
              message.error(err instanceof Error ? err.message : '解压失败');
            } finally {
              setOperation(null);
            }
            e.target.value = '';
          }}
        />
        <Button icon={<FileZipOutlined />} onClick={() => { if (!operation) zipInputRef.current?.click(); }}
          loading={operation === 'zip-upload'} disabled={operation !== null}
          style={{ borderColor: '#eb2f96', color: '#eb2f96', fontWeight: 500 }}>
          上传压缩包
        </Button>
        <Typography.Text type="secondary" className="upload-toolbar-hint">
          支持 {p.acceptExt} / .zip 格式，单文件最大 512 MB，{p.uploadHint}
        </Typography.Text>
        {uploadProgress && uploadProgress.total > 0 && (
          <div style={{ minWidth: 180 }}>
            <Progress
              percent={Math.round((uploadProgress.processed / uploadProgress.total) * 100)}
              size="small"
              format={() => `${uploadProgress.processed}/${uploadProgress.total}`}
            />
          </div>
        )}
        {selectedBatch && (
          <Tag color="purple" style={{ marginLeft: 'auto' }}>当前：{selectedBatch}</Tag>
        )}
      </div>

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
