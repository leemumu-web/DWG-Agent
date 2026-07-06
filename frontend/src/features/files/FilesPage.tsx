import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import {
  listFiles,
  listBatches,
  downloadFile,
  bulkDeleteFiles,
  uploadFolder,
} from '../../api/files.api';
import { listJobs, getJobResults, retryJob, cancelAllJobs, createDxfJob } from '../../api/jobs.api';
import { FileUpload } from '../../components/FileUpload';
import { ZipDownloadModal } from '../../components/ZipDownloadModal';
import type { BatchInfo, StoredFile } from '../../types/file';
import type { Job } from '../../types/job';

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

export function FilesPage() {
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);
  const [selectedBatchNames, setSelectedBatchNames] = useState<string[]>([]);
  const [zipModalOpen, setZipModalOpen] = useState(false);
  const [batchZipModalOpen, setBatchZipModalOpen] = useState(false);
  const [pauseLoading, setPauseLoading] = useState(false);
  const folderInputRef = useRef<HTMLInputElement>(null);

  // ── data ──────────────────────────────────────────────────────────────────
  const filesQ = useQuery({
    queryKey: ['files', selectedBatch],
    queryFn: ({ queryKey }) => listFiles(queryKey[1] as string | undefined),
    staleTime: 2000,
  });
  const batchesQ = useQuery({
    queryKey: ['batches'],
    queryFn: listBatches,
    staleTime: 5000,
    enabled: selectedBatch === null,
  });
  const jobsQ = useQuery({ queryKey: ['jobs'], queryFn: listJobs, staleTime: 2000 });

  // All files (now includes both DWG and DXF from all buckets, not just dwg-original)
  const allFiles = filesQ.data ?? [];

  // Split: DWG source files and DXF results for display
  const dwgFiles = useMemo(
    () => allFiles.filter((f) => f.file_ext === '.dwg'),
    [allFiles],
  );

  const hasActive = useMemo(
    () => (jobsQ.data ?? []).some((j) => j.status === 'queued' || j.status === 'running'),
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
    for (const j of jobsQ.data ?? []) {
      const fid = (j.params_json as Record<string, unknown> | null)?.file_id as number | undefined;
      if (fid) map.set(fid, j);
    }
    return map;
  }, [jobsQ.data]);

  // Files that need (re-)conversion: no job OR job failed/cancelled
  const pendingFiles = useMemo(
    () => dwgFiles.filter((f) => {
      const j = jobsByFileId.get(f.id);
      return !j || j.status === 'failed' || j.status === 'cancelled';
    }),
    [dwgFiles, jobsByFileId],
  );

  const refresh = useCallback(() => { filesQ.refetch(); jobsQ.refetch(); batchesQ.refetch(); }, [filesQ, jobsQ, batchesQ]);

  // ── single file actions ───────────────────────────────────────────────────
  const handleDownload = useCallback(async (file: StoredFile) => {
    try { await downloadFile(file.id, file.original_name); } catch (err) { message.error(err instanceof Error ? err.message : '下载失败'); }
  }, []);

  const handleDownloadDxf = useCallback(async (job: Job, sourceName: string) => {
    try {
      const results = await getJobResults(job.id);
      const dxf = results.find((r) => r.result_type === 'convert_dwg_to_dxf');
      if (!dxf?.result_file_id) { message.error('DXF 结果未找到'); return; }
      await downloadFile(dxf.result_file_id, sourceName.replace(/\.dwg$/i, '.dxf'));
    } catch (err) { message.error(err instanceof Error ? err.message : '获取 DXF 失败'); }
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
      const queue = [...pendingFiles];
      let count = 0;
      const worker = async () => {
        while (queue.length > 0) {
          const f = queue.shift()!;
          try { await createDxfJob(f.id); count++; } catch { /* skip failures */ }
        }
      };
      await Promise.all(Array.from({ length: Math.min(3, queue.length || 1) }, () => worker()));
      if (count > 0) {
        message.success(`已提交 ${count} 个转换任务`);
      } else {
        message.info('没有需要转换的文件');
      }
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
      const files = await listFiles(bn);
      for (const f of files) allIds.push(f.id);
    }
    if (allIds.length === 0) { message.warning('所选文件夹中没有文件'); return; }
    setBatchZipFileIds(allIds);
    setBatchZipModalOpen(true);
  }, [selectedBatchNames]);

  const handleBatchDelete = useCallback(async () => {
    const allIds: number[] = [];
    for (const bn of selectedBatchNames) {
      const files = await listFiles(bn);
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
  const totalSize = allFiles.reduce((s, f) => s + f.size_bytes, 0);
  const isFirstLoad = filesQ.isLoading && (filesQ.data ?? []).length === 0;

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
      title: '类型', dataIndex: 'file_ext', width: 50, align: 'center' as const,
      render: (ext: string) => (
        <Tag style={{ margin: 0, borderRadius: 4 }} color={ext === '.dwg' ? 'blue' : 'green'}>
          {ext.replace('.', '').toUpperCase()}
        </Tag>
      ),
    },
    {
      title: '文件名', dataIndex: 'original_name',
      render: (name: string) => (
        <Space>
          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 32, height: 32, borderRadius: 8, background: '#f5f5f5' }}>
            <FileOutlined style={{ color: '#8c8c8c', fontSize: 15 }} />
          </span>
          <Tooltip title={name}><Typography.Text style={{ maxWidth: 300 }} ellipsis>{name}</Typography.Text></Tooltip>
        </Space>
      ),
    },
    {
      title: '大小', dataIndex: 'size_bytes', width: 90, align: 'right' as const,
      render: (v: number) => <Typography.Text type="secondary">{fmtSize(v)}</Typography.Text>,
    },
    {
      title: '转换状态', width: 180,
      render: (_: unknown, record: StoredFile) => {
        if (record.file_ext !== '.dwg') return <Typography.Text type="secondary">—</Typography.Text>;
        const job = jobsByFileId.get(record.id);
        if (!job) return <Typography.Text type="secondary">未转换</Typography.Text>;
        const s = STATUS[job.status] ?? STATUS.cancelled;
        return (
          <Space size={8}>
            <Tag style={{ color: s.color, background: s.bg, border: 'none', borderRadius: 6 }}>
              {s.icon} <span style={{ marginLeft: 4 }}>{s.label}</span>
            </Tag>
            <Progress percent={job.progress} size="small" style={{ width: 56, margin: 0 }}
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
        if (record.file_ext !== '.dwg') {
          return (
            <Tooltip title="下载 DXF">
              <Button type="text" size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(record)} />
            </Tooltip>
          );
        }
        const job = jobsByFileId.get(record.id);
        const isSucceeded = job?.status === 'succeeded';
        const isFailed = job?.status === 'failed' || job?.status === 'cancelled';
        return (
          <Space size={2}>
            <Tooltip title="下载 DWG">
              <Button type="text" size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(record)} />
            </Tooltip>
            {isSucceeded && job && (
              <Tooltip title="下载 DXF">
                <Button type="text" size="small" icon={<FileTextOutlined style={{ color: '#1677ff' }} />}
                  onClick={() => handleDownloadDxf(job, record.original_name)} />
              </Tooltip>
            )}
            {isFailed && job && (
              <Tooltip title="重试转换">
                <Button type="text" size="small" danger icon={<ReloadOutlined />} onClick={() => handleRetry(job.id)} />
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
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          {selectedBatch ? (
            <Space size={4}>
              <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => { setSelectedBatch(null); setSelectedRowKeys([]); }}>
                返回
              </Button>
              <Breadcrumb items={[
                { title: <a onClick={() => { setSelectedBatch(null); setSelectedRowKeys([]); }}>全部文件</a> },
                { title: <Space><FolderOutlined />{selectedBatch}</Space> },
              ]} />
            </Space>
          ) : (
            <>
              <Typography.Title level={4} style={{ margin: 0 }}>文件管理</Typography.Title>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                上传文件夹组织 DWG 图纸，自动转换为 DXF 格式
              </Typography.Text>
            </>
          )}
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

      {/* ── stats ─────────────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        {[
          { label: '全部文件', value: allFiles.length, icon: <FileOutlined />, color: '#1677ff', bg: '#e6f4ff' },
          { label: '已转换 DXF', value: succeeded, icon: <CheckCircleFilled />, color: '#52c41a', bg: '#f6ffed' },
          { label: '处理中', value: processing, icon: <SyncOutlined spin={processing > 0} />, color: '#faad14', bg: '#fffbe6' },
          { label: '存储总量', value: fmtSize(totalSize), icon: <CloudOutlined />, color: '#722ed1', bg: '#f9f0ff' },
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

      {/* ── batch/folder view (top level only) ──────────────────────── */}
      {selectedBatch === null && (batchesQ.data ?? []).length > 0 && (
        <div style={{ marginBottom: 20 }}>
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
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
            {(batchesQ.data ?? []).map((b: BatchInfo) => {
              const isChecked = selectedBatchNames.includes(b.name);
              return (
                <Card
                  key={b.name}
                  hoverable
                  size="small"
                  style={{
                    cursor: 'pointer',
                    border: isChecked ? '2px solid #1677ff' : undefined,
                    background: isChecked ? '#e6f4ff' : undefined,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                    <Checkbox
                      checked={isChecked}
                      onChange={() => toggleBatchSelection(b.name)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <div
                      style={{ flex: 1, minWidth: 0, cursor: 'pointer' }}
                      onClick={() => setSelectedBatch(b.name)}
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
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '8px 16px', marginBottom: 12,
          background: '#e6f4ff', borderRadius: 8,
        }}>
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
      <FileUpload onUploaded={refresh} batchName={selectedBatch ?? undefined} />
      <div style={{ marginBottom: 20 }}>
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
              // Derive batch name: prefer current batch context, then folder path
              const firstPath = (files[0] as { webkitRelativePath?: string }).webkitRelativePath || '';
              const folderName = selectedBatch || firstPath.split('/')[0] || `导入_${Date.now()}`;
              const result = await uploadFolder(files, folderName);
              if (result.success > 0) {
                message.success(`已导入 ${result.success}/${result.total} 个文件到 "${folderName}"`);
                refresh();
              } else if (result.total > 0) {
                message.error(`全部 ${result.total} 个文件上传失败`);
              } else {
                message.warning('文件夹中没有 .dwg 文件');
              }
              e.target.value = '';
            }
          }}
        />
        <Space>
          <Button icon={<FolderOpenOutlined />} onClick={handleFolderClick}>
            上传文件夹
          </Button>
          {selectedBatch && (
            <Typography.Text type="secondary">
              文件将添加到 "{selectedBatch}"
            </Typography.Text>
          )}
        </Space>
      </div>

      {/* ── bulk action bar ──────────────────────────────────────────── */}
      {selectedRowKeys.length > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '8px 16px', marginBottom: 12,
          background: '#e6f4ff', borderRadius: 8,
        }}>
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
        rowKey="id"
        dataSource={allFiles}
        columns={columns}
        rowSelection={rowSelection}
        loading={isFirstLoad}
        size="middle"
        pagination={{
          defaultPageSize: 15,
          pageSizeOptions: [10, 15, 20, 30, 50, 100],
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (t, range) => `${range[0]}-${range[1]} / 共 ${t} 个`,
        }}
        locale={{
          emptyText: (
            <div style={{ padding: '48px 0' }}>
              <InboxOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />
              <p style={{ fontSize: 14, color: '#bfbfbf', marginTop: 12 }}>暂无文件</p>
              <p style={{ fontSize: 12, color: '#d9d9d9' }}>
                {selectedBatch ? '拖拽 DWG 到上方区域添加文件' : '上传文件夹或拖拽 DWG 文件到上方区域'}
              </p>
            </div>
          ),
        }}
        style={{ background: '#fff', borderRadius: 10 }}
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
