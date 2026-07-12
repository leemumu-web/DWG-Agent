import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FileUpload } from './FileUpload';
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
  InboxOutlined,
  CloudOutlined,
  FileZipOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import {
  listFiles,
  listFilesPage,
  listBatches,
  downloadFile,
  bulkDeleteFiles,
  uploadFolder,
  uploadFile,
  uploadZip,
} from '../api/files.api';
import { listJobsPage, getJobResults, retryJob, cancelAllJobs } from '../api/jobs.api';
import { ZipDownloadModal } from '../components/ZipDownloadModal';
import type { BatchInfo, StoredFile } from '../types/file';
import type { Job } from '../types/job';

// ── helpers ──────────────────────────────────────────────────────────────────

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const STATUS: Record<string, { color: string; bg: string; label: string; icon: React.ReactNode }> = {
  succeeded: { color: '#52c41a', bg: '#f6ffed', label: '已完成', icon: <CheckCircleFilled style={{ color: '#52c41a' }} /> },
  running:   { color: '#1677ff', bg: '#e6f4ff', label: '转换中', icon: <SyncOutlined style={{ color: '#1677ff' }} spin /> },
  queued:    { color: '#faad14', bg: '#fffbe6', label: '排队中', icon: <SyncOutlined style={{ color: '#faad14' }} /> },
  failed:    { color: '#ff4d4f', bg: '#fff2f0', label: '失败',   icon: <CloseCircleFilled style={{ color: '#ff4d4f' }} /> },
  cancelled: { color: '#8c8c8c', bg: '#fafafa', label: '已取消', icon: <CloseCircleFilled style={{ color: '#8c8c8c' }} /> },
};

// ── page ─────────────────────────────────────────────────────────────────────

export interface ConversionPageProps {
  fileExt: string;
  resultExt: string;
  taskType: string;
  resultType: string;
  createJobFn: (fileId: number) => Promise<Job>;
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
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);
  const [selectedBatchNames, setSelectedBatchNames] = useState<string[]>([]);
  const [zipModalOpen, setZipModalOpen] = useState(false);
  const [batchZipModalOpen, setBatchZipModalOpen] = useState(false);
  const [pauseLoading, setPauseLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);

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
  const currentFileIds = (filesQ.data?.data ?? []).map((file) => file.id).join(',');
  const jobsQ = useQuery({
    queryKey: ['jobs', p.taskType, currentFileIds],
    queryFn: () => listJobsPage({
      page: 1,
      page_size: 200,
      task_type: p.taskType,
      file_ids: currentFileIds,
    }),
    staleTime: 2000,
    enabled: Boolean(currentFileIds),
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
    () => (jobsQ.data?.data ?? []).some((j) => j.status === 'queued' || j.status === 'running'),
    [jobsQ.data],
  );

  // Smart polling
  useEffect(() => {
    if (!hasActive) return;
    const id = setInterval(() => { filesQ.refetch(); jobsQ.refetch(); }, 2000);
    return () => clearInterval(id);
  }, [hasActive, filesQ, jobsQ]);

  // file_id → latest Job
  const jobsByFileId = useMemo(() => {
    const map = new Map<number, Job>();
    for (const j of jobsQ.data?.data ?? []) {
      const fid = (j.params_json as Record<string, unknown> | null)?.file_id as number | undefined;
      if (fid && !map.has(fid)) map.set(fid, j);
    }
    return map;
  }, [jobsQ.data]);

  // Periodic clock tick so stuck-queued detection stays fresh.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 15_000);
    return () => clearInterval(id);
  }, []);

  // Files that need (re-)conversion:
  //   no job, failed, cancelled, OR stuck-queued (>60s old, no progress)
  const pendingFiles = useMemo(
    () => {
      const now = Date.now();
      return dwgFiles.filter((f) => {
        const j = jobsByFileId.get(f.id);
        if (!j) return true;
        if (j.status === 'failed' || j.status === 'cancelled') return true;
        if (j.status === 'queued' && j.progress === 0) {
          const age = now - new Date(j.created_at).getTime();
          if (age > 60_000) return true;
        }
        return false;
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dwgFiles, jobsByFileId, tick],
  );

  const refresh = useCallback(() => { filesQ.refetch(); jobsQ.refetch(); batchesQ.refetch(); }, [filesQ, jobsQ, batchesQ]);

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
      const res = await cancelAllJobs();
      message.success(`已暂停 ${res.cancelled_count} 个任务`);
      refresh();
    } catch (err) { message.error(err instanceof Error ? err.message : '暂停失败'); }
    setPauseLoading(false);
  }, [refresh]);

  const handleResumeAll = useCallback(async () => {
    setPauseLoading(true);
    try {
      // 1. Cancel all stuck queued/running jobs
      await cancelAllJobs();
      // 2. Wait for DB to settle, then refresh to get latest state
      await new Promise((r) => setTimeout(r, 1000));
      // 3. Re-submit jobs for pending files (3 concurrent pool)
      const targets = [...pendingFiles];
      if (targets.length === 0) {
        message.info('没有需要转换的文件');
        refresh();
        setPauseLoading(false);
        return;
      }
      let count = 0;
      const queue = [...targets];
      const worker = async () => {
        while (queue.length > 0) {
          const f = queue.shift()!;
          try { await p.createJobFn(f.id); count++; } catch { /* skip */ }
        }
      };
      await Promise.all(
        Array.from({ length: Math.min(3, targets.length) }, () => worker()),
      );
      message.success(`已提交 ${count} 个转换任务`);
      refresh();
    } catch (err) { message.error(err instanceof Error ? err.message : '提交失败'); }
    setPauseLoading(false);
  }, [pendingFiles, refresh]);

  // ── batch-level actions ──────────────────────────────────────────────────
  const toggleBatchSelection = (name: string) => {
    setSelectedBatchNames((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
    setSelectedRowKeys([]);
  };

  const handleBatchDownload = useCallback(async () => {
    const allIds: number[] = [];
    for (const bn of selectedBatchNames) {
      const files = await listFiles(bn, p.fileExt);
      for (const f of files) allIds.push(f.id);
    }
    if (allIds.length === 0) { message.warning('所选文件夹中没有文件'); return; }
    setBatchZipFileIds(allIds);
    setBatchZipModalOpen(true);
  }, [selectedBatchNames]);

  const handleBatchDelete = useCallback(async () => {
    const allIds: number[] = [];
    for (const bn of selectedBatchNames) {
      const files = await listFiles(bn, p.fileExt);
      for (const f of files) allIds.push(f.id);
    }
    if (allIds.length === 0) { message.warning('所选文件夹中没有文件'); return; }
    try {
      await bulkDeleteFiles(allIds);
      message.success(`已删除 ${selectedBatchNames.length} 个文件夹（${allIds.length} 个文件）`);
      setSelectedBatchNames([]);
      refresh();
    } catch (err) { message.error(err instanceof Error ? err.message : '批量删除失败'); }
  }, [selectedBatchNames, refresh]);

  // ── batch zip file IDs ───────────────────────────────────────────────────
  const [batchZipFileIds, setBatchZipFileIds] = useState<number[]>([]);

  // ── folder upload ────────────────────────────────────────────────────────
  const handleFolderClick = () => folderInputRef.current?.click();

  // ── stats ─────────────────────────────────────────────────────────────────
  const succeeded = dwgFiles.filter((f) => jobsByFileId.get(f.id)?.status === 'succeeded').length;
  const processing = dwgFiles.filter((f) => {
    const s = jobsByFileId.get(f.id)?.status; return s === 'running' || s === 'queued';
  }).length;
  const totalSize = dwgFiles.reduce((s, f) => s + f.size_bytes, 0);
  const isFirstLoad = filesQ.isLoading;

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
        const job = jobsByFileId.get(record.id);
        const done = job?.status === 'succeeded';
        return (
          <Space>
            <Tag
              style={{ margin: 0, borderRadius: 4, fontSize: 11, lineHeight: '18px', padding: '0 6px' }}
              color={done ? 'success' : 'processing'}
            >
              {done ? p.tagDone : p.tagPending}
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
        const job = jobsByFileId.get(record.id);
        if (!job) return <Typography.Text type="secondary">未转换</Typography.Text>;
        const s = STATUS[job.status] ?? STATUS.cancelled;
        return (
          <Space size={8}>
            <Tag style={{ color: s.color, background: s.bg, border: 'none', borderRadius: 6 }}>
              {s.icon} <span style={{ marginLeft: 4 }}>{s.label}</span>
            </Tag>
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
      title: '操作', width: 100, align: 'center' as const,
      render: (_: unknown, record: StoredFile) => {
        const job = jobsByFileId.get(record.id);
        const isSucceeded = job?.status === 'succeeded';
        const isFailed = job?.status === 'failed' || job?.status === 'cancelled';
        return (
          <Space size={2}>
            <Tooltip title={`下载 ${p.tagPending}`}>
              <Button aria-label={`下载 ${p.tagPending}`} type="text" size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(record)} />
            </Tooltip>
            {isSucceeded && job && (
              <Tooltip title={p.downloadResultLabel}>
                <Button aria-label={p.downloadResultLabel} type="text" size="small" icon={<FileTextOutlined style={{ color: '#1677ff' }} />}
                  onClick={() => handleDownloadResult(job, record.original_name)} />
              </Tooltip>
            )}
            {isFailed && job && (
              <Tooltip title="重试转换">
                <Button aria-label="重试转换" type="text" size="small" danger icon={<ReloadOutlined />} onClick={() => handleRetry(job.id)} />
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
          {!hasActive && pendingFiles.length > 0 && (
            <Button type="primary" icon={<SyncOutlined />} loading={pauseLoading} onClick={handleResumeAll}>
              继续任务 ({pendingFiles.length})
            </Button>
          )}
        </Space>
      </div>

      {/* ── master progress ────────────────────────────────────────────── */}
      {dwgFiles.length > 0 && (
        <div className="conversion-progress">
          <SyncOutlined spin={processing > 0} style={{ fontSize: 20, color: processing > 0 ? '#1677ff' : '#52c41a' }} />
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <Typography.Text strong>当前页转换进度</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>{succeeded} / {dwgFiles.length} · {dwgFiles.length > 0 ? Math.round((succeeded / dwgFiles.length) * 100) : 0}%</Typography.Text>
            </div>
            <Progress percent={dwgFiles.length > 0 ? Math.round((succeeded / dwgFiles.length) * 100) : 0} strokeColor={{ '0%': '#1677ff', '100%': '#52c41a' }} strokeWidth={8} showInfo={false} />
          </div>
        </div>
      )}

      {/* ── stats ─────────────────────────────────────────────────────── */}
      <div className="conversion-stats">
        {[
          { label: `${p.title}总数`, value: filesQ.data?.pagination.total ?? 0, icon: <FileOutlined />, color: '#2563eb', bg: '#eff6ff' },
          { label: `本页已转换 ${p.tagDone}`, value: succeeded, icon: <CheckCircleFilled />, color: '#059669', bg: '#ecfdf5' },
          { label: '本页处理中', value: processing, icon: <SyncOutlined spin={processing > 0} />, color: '#d97706', bg: '#fffbeb' },
          { label: '本页存储量', value: fmtSize(totalSize), icon: <CloudOutlined />, color: '#7c3aed', bg: '#f5f3ff' },
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
      {selectedBatch === null && (batchesQ.data ?? []).length > 0 && (
        <div className="folder-section">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <Typography.Text strong style={{ fontSize: 14 }}>
              <FolderOpenOutlined style={{ marginRight: 6 }} />文件夹
              <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                （上传文件夹时自动创建，勾选后可打包下载或删除）
              </Typography.Text>
            </Typography.Text>
            {selectedBatchNames.length > 0 && (
              <Button size="small" onClick={() => setSelectedBatchNames([])}>取消选择</Button>
            )}
          </div>
          <div className="folder-grid">
            {(batchesQ.data ?? []).map((b: BatchInfo) => {
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
                      checked={isChecked}
                      onChange={() => toggleBatchSelection(b.name)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <div
                      style={{ flex: 1, minWidth: 0, cursor: 'pointer' }}
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
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* ── batch action bar ─────────────────────────────────────────── */}
      {selectedBatchNames.length > 0 && (
        <div className="selection-bar">
          <Typography.Text strong style={{ marginRight: 8 }}>
            已选 {selectedBatchNames.length} 个文件夹
          </Typography.Text>
          <Button type="primary" size="small" icon={<DownloadOutlined />}
            onClick={handleBatchDownload}>
            打包下载 (.zip)
          </Button>
          <Popconfirm
            title={`确认删除 ${selectedBatchNames.length} 个文件夹及其所有文件？`}
            description="此操作不可撤销，文件夹内所有文件将被删除"
            onConfirm={handleBatchDelete}
            okText="确认删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>删除文件夹</Button>
          </Popconfirm>
        </div>
      )}

      {/* ── upload area ──────────────────────────────────────────────── */}
      <div className="upload-toolbar">
        <input
          ref={folderInputRef}
          type="file"
          /* @ts-expect-error webkitdirectory */
          webkitdirectory=""
          multiple
          style={{ display: 'none' }}
          onChange={async (e) => {
            const raw = e.target.files;
            if (raw && raw.length > 0) {
              const files = Array.from(raw);
              const firstPath = (files[0] as { webkitRelativePath?: string }).webkitRelativePath || '';
              const folderName = selectedBatch || firstPath.split('/')[0] || `导入_${Date.now()}`;
              const result = await uploadFolder(files, folderName, {
                fileExt: p.acceptExt,
                onFile: async (file: File, bn: string) => {
                  const stored = await uploadFile(file, bn);
                  return p.createJobFn(stored.id);
                },
              });
              if (result.success > 0) {
                message.success(`已导入 ${result.success}/${result.total} 个文件到 "${folderName}"`);
                refresh();
              } else if (result.total > 0) {
                message.error(`全部 ${result.total} 个文件上传失败`);
              } else {
                message.warning(`文件夹中没有 ${p.fileExt} 文件`);
              }
              e.target.value = '';
            }
          }}
        />
        <FileUpload onUploaded={refresh} batchName={selectedBatch ?? undefined} acceptExt={p.acceptExt} label={`上传 ${p.tagPending} 文件`} uploadFn={async (file: File, bn?: string) => { const stored = await uploadFile(file, bn); return p.createJobFn(stored.id); }} />
        <Button icon={<FolderOpenOutlined />} onClick={handleFolderClick} style={{ borderColor: '#722ed1', color: '#722ed1', fontWeight: 500 }}>
          上传文件夹
        </Button>
        <input
          ref={zipInputRef}
          type="file"
          accept=".zip"
          style={{ display: 'none' }}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            try {
              const result = await uploadZip(file, p.acceptExt);
              if (result.success_count > 0) {
                message.success(`已解压 ${result.success_count}/${result.success_count + result.skipped_count} 个文件到 "${result.batch_name}"`);
                // Auto-create conversion jobs
                const queue = [...result.files];
                let count = 0;
                const worker = async () => {
                  while (queue.length > 0) {
                    const f = queue.shift()!;
                    try { await p.createJobFn(f.id); count++; } catch { /* skip */ }
                  }
                };
                await Promise.all(Array.from({ length: Math.min(3, result.files.length) }, () => worker()));
                message.success(`${count} 个文件已提交转换`);
                refresh();
              } else {
                message.warning(`压缩包中没有 ${p.acceptExt} 文件`);
              }
            } catch (err) {
              message.error(err instanceof Error ? err.message : '解压失败');
            }
            e.target.value = '';
          }}
        />
        <Button icon={<FileZipOutlined />} onClick={() => zipInputRef.current?.click()}
          style={{ borderColor: '#eb2f96', color: '#eb2f96', fontWeight: 500 }}>
          上传压缩包
        </Button>
        <Typography.Text type="secondary" className="upload-toolbar-hint">
          支持 {p.acceptExt} / .zip 格式，单文件最大 512 MB，{p.uploadHint}
        </Typography.Text>
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
        onClose={() => setZipModalOpen(false)}
        onDone={() => { setSelectedRowKeys([]); refresh(); }}
      />
      <ZipDownloadModal
        open={batchZipModalOpen}
        fileIds={batchZipFileIds}
        fileCount={batchZipFileIds.length}
        onClose={() => { setBatchZipModalOpen(false); setSelectedBatchNames([]); }}
        onDone={() => { setBatchZipModalOpen(false); setSelectedBatchNames([]); refresh(); }}
      />
    </>
  );
}
