import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Card,
  Checkbox,
  Popconfirm,
  message,
  Progress,
  Space,
  Tag,
  Tooltip,
  Typography,
  Alert,
} from 'antd';
import {
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  ReloadOutlined,
  FileTextOutlined,
  CheckCircleFilled,
  SyncOutlined,
  CloseCircleFilled,
  CloudOutlined,
  FileZipOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  CloseOutlined,
  FileExcelOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import {
  listBatches,
  listFiles,
  uploadZip,
  uploadFolder,
  uploadFile,
  deleteBatch,
  downloadBatchZip,
} from '../../api/files.api';
import { listJobs, getJobResults, createDxf2ExcelJob, retryJob, cancelJob } from '../../api/jobs.api';
import ExcelPreview from '../../components/ExcelPreview';
import type { BatchInfo } from '../../types/file';
import type { Job } from '../../types/job';

// ── helpers ──────────────────────────────────────────────────────────────────

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const STATUS: Record<string, { color: string; bg: string; label: string; icon: React.ReactNode }> = {
  succeeded: { color: '#52c41a', bg: '#f6ffed', label: '已完成', icon: <CheckCircleFilled style={{ color: '#52c41a' }} /> },
  running:   { color: '#1677ff', bg: '#e6f4ff', label: '提取中', icon: <SyncOutlined style={{ color: '#1677ff' }} spin /> },
  queued:    { color: '#faad14', bg: '#fffbe6', label: '排队中', icon: <SyncOutlined style={{ color: '#faad14' }} /> },
  failed:    { color: '#ff4d4f', bg: '#fff2f0', label: '失败',   icon: <CloseCircleFilled style={{ color: '#ff4d4f' }} /> },
  cancelled: { color: '#8c8c8c', bg: '#fafafa', label: '已取消', icon: <CloseCircleFilled style={{ color: '#8c8c8c' }} /> },
};

// ── page ──────────────────────────────────────────────────────────────────────

export function Dxf2ExcelPage() {
  const folderInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);

  // Multi-select state
  const [selectedBatches, setSelectedBatches] = useState<Set<string>>(new Set());
  // Batches whose cancelled/failed jobs have been explicitly cleared by the user
  const [clearedBatches, setClearedBatches] = useState<Set<string>>(new Set());
  // Excel preview modal state
  const [previewFileId, setPreviewFileId] = useState<number | null>(null);
  const [previewFileName, setPreviewFileName] = useState<string>('');

  // ── data ──────────────────────────────────────────────────────────────────
  const batchesQ = useQuery({
    queryKey: ['batches', '.dxf'],
    queryFn: ({ queryKey }) => listBatches(queryKey[1] as string),
    staleTime: 3000,
  });
  const jobsQ = useQuery({
    queryKey: ['jobs', 'extract_dxf_to_excel'],
    queryFn: ({ queryKey }) => listJobs(queryKey[1] as string),
    staleTime: 2000,
  });

  const batches = batchesQ.data ?? [];
  const allJobs = jobsQ.data ?? [];

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
    } catch (err) { message.error(err instanceof Error ? err.message : '提交失败'); }
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
    } catch (err) { message.error(err instanceof Error ? err.message : '获取预览失败'); }
  }, [jobsByBatch]);

  const handleDownloadExcel = useCallback(async (batchName: string) => {
    try {
      const job = jobsByBatch.get(batchName);
      if (!job) { message.error('未找到提取任务'); return; }
      const results = await getJobResults(job.id);
      const excel = results.find((r) => r.result_type === 'extract_dxf_to_excel');
      if (!excel?.result_file_id) { message.error('Excel 结果未找到'); return; }
      const { downloadFile } = await import('../../api/files.api');
      await downloadFile(excel.result_file_id, `${batchName}.xlsx`);
    } catch (err) { message.error(err instanceof Error ? err.message : '下载失败'); }
  }, [jobsByBatch]);

  const handleRetry = useCallback(async (batchName: string) => {
    try {
      const job = jobsByBatch.get(batchName);
      if (!job) { message.error('未找到提取任务'); return; }
      await retryJob(job.id);
      message.success('已重新提交');
      refresh();
    } catch (err) { message.error(err instanceof Error ? err.message : '重试失败'); }
  }, [jobsByBatch, refresh]);

  const handleCancel = useCallback(async (batchName: string) => {
    try {
      const job = jobsByBatch.get(batchName);
      if (!job) { message.error('未找到提取任务'); return; }
      await cancelJob(job.id);
      message.success(`已暂停: ${batchName}（可点击"重试提取"重新开始）`);
      refresh();
    } catch (err) { message.error(err instanceof Error ? err.message : '暂停失败'); }
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
    } catch (err) { message.error(err instanceof Error ? err.message : '取消失败'); }
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
    } catch (err) { message.error(err instanceof Error ? err.message : '删除失败'); }
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
    } catch (err) { message.error(err instanceof Error ? err.message : '批量删除失败'); }
  }, [selectedArr, jobsByBatch, refresh]);

  const handleBulkDownload = useCallback(async () => {
    if (selectedArr.length === 1) {
      // Single batch — download directly
      try {
        await downloadBatchZip(selectedArr[0]);
      } catch (err) { message.error(err instanceof Error ? err.message : '下载失败'); }
    } else {
      // Multiple batches — download each one sequentially
      let count = 0;
      for (const bn of selectedArr) {
        try {
          await downloadBatchZip(bn);
          count++;
        } catch { /* continue */ }
      }
      if (count > 0) message.success(`已下载 ${count} 个批次`);
    }
  }, [selectedArr]);

  // ── folder upload ────────────────────────────────────────────────────────
  const handleFolderClick = () => folderInputRef.current?.click();

  // ── error state ───────────────────────────────────────────────────────────
  if (batchesQ.isError && !batchesQ.data) {
    return (
      <Alert type="error" message="加载批次列表失败"
        description={(batchesQ.error as Error)?.message || '请检查后端服务是否正常运行'}
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

      {/* ── upload area ──────────────────────────────────────────────────── */}
      <div style={{ background: '#fafcff', border: '1px solid #e8ecf1', borderRadius: 10, padding: '12px 16px', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
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
              const folderName = firstPath.split('/')[0] || `导入_${Date.now()}`;
              const result = await uploadFolder(files, folderName, {
                fileExt: '.dxf',
                onFile: async (file: File, bn: string) => uploadFile(file, bn),
              });
              if (result.success > 0) {
                message.success(`已导入 ${result.success}/${result.total} 个文件到 "${folderName}"`);
                refresh();
              } else if (result.total > 0) {
                message.error(`全部 ${result.total} 个文件上传失败`);
              } else {
                message.warning('文件夹中没有 .dxf 文件');
              }
              e.target.value = '';
            }
          }}
        />
        <Button icon={<FolderOpenOutlined />} onClick={handleFolderClick}
          style={{ borderColor: '#722ed1', color: '#722ed1', fontWeight: 500 }}>
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
              const result = await uploadZip(file, '.dxf');
              if (result.success_count > 0) {
                message.success(`已解压 ${result.success_count}/${result.success_count + result.skipped_count} 个文件到 "${result.batch_name}"`);
                refresh();
              } else {
                message.warning('压缩包中没有 .dxf 文件');
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
        <Typography.Text type="secondary">
          支持 .dxf / .zip 格式，单文件最大 512 MB，每个文件夹将生成一个 Excel
        </Typography.Text>
      </div>

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
          {batches.map((b: BatchInfo) => {
            const job = jobsByBatch.get(b.name);
            const s = job ? (STATUS[job.status] ?? STATUS.cancelled) : null;
            const isDone = job?.status === 'succeeded';
            const isFailed = job?.status === 'failed' || job?.status === 'cancelled';
            const isProcessing = job?.status === 'running' || job?.status === 'queued';
            const hasNoJob = !job;
            const isSelected = selectedBatches.has(b.name);

            return (
              <Card
                key={b.name}
                hoverable
                size="small"
                style={{
                  borderRadius: 10,
                  border: isSelected ? '2px solid #1677ff' : undefined,
                  background: isSelected ? '#e6f4ff' : undefined,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                  {/* Checkbox */}
                  <Checkbox
                    checked={isSelected}
                    onChange={() => toggleBatch(b.name)}
                    style={{ marginTop: 4 }}
                  />

                  {/* Folder icon */}
                  <FolderOutlined style={{ fontSize: 28, color: '#faad14', marginTop: 2 }} />

                  <div style={{ flex: 1, minWidth: 0 }}>
                    {/* Batch name */}
                    <Tooltip title={b.name}>
                      <Typography.Text strong ellipsis style={{ fontSize: 14, maxWidth: 180, display: 'block' }}>
                        {b.name}
                      </Typography.Text>
                    </Tooltip>

                    {/* File count + size + time */}
                    <div style={{ marginTop: 4 }}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        <FileTextOutlined style={{ marginRight: 4 }} />
                        {b.file_count} 个 DXF 文件
                      </Typography.Text>
                    </div>
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      {new Date(b.latest_created_at).toLocaleString('zh-CN', {
                        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                      })}
                    </Typography.Text>

                    {/* Job status */}
                    <div style={{ marginTop: 10 }}>
                      {/* Processing state — progress bar + pause button */}
                      {isProcessing && job && (
                        <div>
                          <Tag style={{ color: s!.color, background: s!.bg, border: 'none', borderRadius: 6, marginBottom: 6 }}>
                            {s!.icon} <span style={{ marginLeft: 4 }}>{s!.label}</span>
                          </Tag>
                          <Progress percent={job.progress} size="small"
                            strokeColor={s!.color}
                            status={job.status === 'failed' ? 'exception' : undefined} />
                          <div style={{ marginTop: 6 }}>
                            <Button size="small" icon={<PauseCircleOutlined />}
                              onClick={() => handleCancel(b.name)}>
                              暂停
                            </Button>
                          </div>
                        </div>
                      )}

                      {/* Done state — status tag */}
                      {isDone && (
                        <Tag style={{ color: s!.color, background: s!.bg, border: 'none', borderRadius: 6 }}>
                          {s!.icon} <span style={{ marginLeft: 4 }}>{s!.label}</span>
                        </Tag>
                      )}

                      {/* Failed / cancelled state */}
                      {isFailed && (
                        <Tag style={{ color: s!.color, background: s!.bg, border: 'none', borderRadius: 6 }}>
                          {s!.icon} <span style={{ marginLeft: 4 }}>{s!.label}</span>
                          {job?.error_code && (
                            <Tooltip title={job.error_message || job.error_code}>
                              <span style={{ marginLeft: 4, fontSize: 11, color: '#8c8c8c' }}>
                                ({job.error_code})
                              </span>
                            </Tooltip>
                          )}
                        </Tag>
                      )}

                      {/* Action buttons */}
                      <div style={{ marginTop: 8 }}>
                        {/* No job yet — start extraction */}
                        {hasNoJob && (
                          <Button type="primary" size="small" icon={<PlayCircleOutlined />}
                            onClick={() => handleProcess(b.name)}>
                            开始提取
                          </Button>
                        )}

                        {/* Done — preview + download + retry + clear */}
                        {isDone && job && (
                          <Space size={4} wrap>
                            <Button type="primary" size="small" icon={<EyeOutlined />}
                              onClick={() => handlePreviewExcel(b.name)}>
                              预览
                            </Button>
                            <Tooltip title="下载 Excel">
                              <Button size="small" icon={<DownloadOutlined />}
                                onClick={() => handleDownloadExcel(b.name)} />
                            </Tooltip>
                            <Tooltip title="重新提取">
                              <Button size="small" icon={<ReloadOutlined />}
                                onClick={() => handleRetry(b.name)} />
                            </Tooltip>
                            <Popconfirm
                              title={`删除批次 "${b.name}"？`}
                              description="批次内所有 .dxf 文件将被删除，此操作不可撤销"
                              onConfirm={() => handleDeleteBatch(b.name)}
                              okText="确认删除"
                              cancelText="取消"
                              okButtonProps={{ danger: true }}
                            >
                              <Button size="small" danger icon={<DeleteOutlined />} />
                            </Popconfirm>
                          </Space>
                        )}

                        {/* Failed/cancelled — retry + clear */}
                        {isFailed && job && (
                          <Space size={4}>
                            <Button size="small" danger icon={<ReloadOutlined />}
                              onClick={() => handleRetry(b.name)}>
                              重试提取
                            </Button>
                            <Button size="small" icon={<CloseOutlined />}
                              onClick={() => handleClear(b.name)}>
                              取消提取
                            </Button>
                            <Popconfirm
                              title={`删除批次 "${b.name}"？`}
                              description="批次内所有 .dxf 文件将被删除"
                              onConfirm={() => handleDeleteBatch(b.name)}
                              okText="确认删除"
                              cancelText="取消"
                              okButtonProps={{ danger: true }}
                            >
                              <Button size="small" danger icon={<DeleteOutlined />} />
                            </Popconfirm>
                          </Space>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              </Card>
            );
          })}
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
