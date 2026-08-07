import { useEffect, useState } from 'react';
import {
  CheckCircleOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Checkbox,
  Modal,
  Space,
  Spin,
  Tooltip,
  Typography,
} from 'antd';
import {
  describeApiError,
  describeDownloadError,
  useDownload,
  type TransferProgress,
} from '../../shared/api';
import { ApiErrorAlert, CancellableDownloadProgress, fmtSize } from '../../shared/components';
import type {
  WorkflowBatchExport,
  WorkflowExportCategory,
} from './workflow';
import {
  createWorkflowBatchExport,
  downloadWorkflowBatchExport,
  getWorkflowBatchExport,
  getWorkflowBatchExportPreview,
  purgeWorkflowBatchExport,
} from './workflows.api';

const ACTIVE_DOWNLOAD_STATUSES = new Set(['prepared', 'downloading']);

export function WorkflowBatchExportControl({
  workflowId,
  disabled = false,
  disabledReason,
  onPurged,
}: {
  workflowId: number;
  disabled?: boolean;
  disabledReason?: string;
  onPurged: () => void;
}) {
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const downloadCtrl = useDownload();
  const [open, setOpen] = useState(false);
  const [selectionInitialized, setSelectionInitialized] = useState(false);
  const [selected, setSelected] = useState<WorkflowExportCategory[]>([]);
  const [createdExport, setCreatedExport] = useState<WorkflowBatchExport | null>(null);
  const [downloadProgress, setDownloadProgress] = useState<TransferProgress | null>(null);

  const previewQ = useQuery({
    queryKey: ['workflow-batch-export-preview', workflowId],
    queryFn: () => getWorkflowBatchExportPreview(workflowId),
    enabled: open && !createdExport,
    staleTime: 0,
    retry: false,
  });

  useEffect(() => {
    if (
      !open
      || !previewQ.data
      || previewQ.isFetching
      || selectionInitialized
    ) return;
    setSelected(
      previewQ.data.categories
        .filter((category) => category.available)
        .map((category) => category.key),
    );
    setSelectionInitialized(true);
  }, [open, previewQ.data, previewQ.isFetching, selectionInitialized]);

  const statusQ = useQuery({
    queryKey: [
      'workflow-batch-export',
      workflowId,
      createdExport?.export_uid,
    ],
    queryFn: () => getWorkflowBatchExport(
      workflowId,
      createdExport!.export_uid,
    ),
    enabled: open && Boolean(createdExport),
    refetchInterval: (query) => {
      const currentStatus = query.state.data?.status ?? createdExport?.status;
      return ACTIVE_DOWNLOAD_STATUSES.has(currentStatus ?? '') ? 1000 : false;
    },
  });
  const exportRow = statusQ.data ?? createdExport;

  const downloadM = useMutation({
    mutationFn: (row: WorkflowBatchExport) => {
      const handle = downloadCtrl.start();
      return downloadWorkflowBatchExport(row, setDownloadProgress, handle.signal)
        .finally(handle.finish);
    },
    onSuccess: () => {
      message.success('分批导出 ZIP 已下载到浏览器');
      setTimeout(() => { void statusQ.refetch(); }, 300);
    },
    onError: (error) => {
      const result = describeDownloadError(error, '分批导出下载失败');
      if (result.cancelled) {
        message.info('下载已取消，服务器文件仍保留，可重新下载');
      } else {
        message.error(result.message);
      }
      void statusQ.refetch();
    },
  });

  const createM = useMutation({
    mutationFn: () => createWorkflowBatchExport(workflowId, selected),
    onSuccess: (next) => {
      setCreatedExport(next);
      queryClient.setQueryData(
        ['workflow-batch-export', workflowId, next.export_uid],
        next,
      );
      setDownloadProgress(null);
      downloadM.mutate(next);
    },
    onError: (error) => message.error(
      describeApiError(error, '分批导出创建失败'),
    ),
  });

  const purgeM = useMutation({
    mutationFn: (exportUid: string) => purgeWorkflowBatchExport(
      workflowId,
      exportUid,
    ),
    onSuccess: (result) => {
      message.success(
        `服务器文件已永久删除，共释放 ${fmtSize(result.released_bytes)}`,
      );
      setOpen(false);
      setCreatedExport(null);
      setSelectionInitialized(false);
      setSelected([]);
      void queryClient.invalidateQueries({
        queryKey: ['workflow-batch-export-preview', workflowId],
      });
      onPurged();
    },
    onError: (error) => message.error(
      describeApiError(error, '服务器文件删除失败，文件仍按当前状态保留'),
    ),
  });

  const show = () => {
    setCreatedExport(null);
    setSelectionInitialized(false);
    setSelected([]);
    setDownloadProgress(null);
    setOpen(true);
  };

  const closeAndRetain = () => {
    if (purgeM.isPending) return;
    if (downloadM.isPending) downloadCtrl.cancel();
    setOpen(false);
    setCreatedExport(null);
    setSelectionInitialized(false);
    setSelected([]);
    setDownloadProgress(null);
  };

  const retryDownload = () => {
    if (!exportRow) return;
    setDownloadProgress(null);
    downloadM.mutate(exportRow);
  };

  const confirmPurge = () => {
    if (!exportRow || exportRow.status !== 'downloaded') return;
    modal.confirm({
      title: '永久删除服务器文件？',
      icon: <DeleteOutlined />,
      width: 560,
      content: (
        <Space orientation="vertical" size={10}>
          <Typography.Text>
            你已确认 ZIP 保存到本地。继续后会物理删除本次所选文件及其 DXF
            预览缓存，释放服务器磁盘空间。
          </Typography.Text>
          <Typography.Text type="danger" strong>
            此操作不可恢复，请先确认本地 ZIP 可以正常打开。
          </Typography.Text>
          <Typography.Text type="secondary">
            数据库仅保留文件名、大小、哈希和生产引用墓碑，不保留可下载的文件字节。
          </Typography.Text>
        </Space>
      ),
      okText: '确认永久删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => purgeM.mutateAsync(exportRow.export_uid),
    });
  };

  const downloadStatus = (() => {
    if (!exportRow) return null;
    if (statusQ.isError) {
      return (
        <ApiErrorAlert
          title="下载状态读取失败"
          error={statusQ.error}
          fallback="下载状态读取失败"
          retryLabel="刷新状态"
          retryLoading={statusQ.isFetching}
          onRetry={() => statusQ.refetch()}
        />
      );
    }
    if (exportRow.status === 'download_failed') {
      return (
        <Alert
          type="error"
          showIcon
          message="ZIP 未完整传输，服务器文件未删除"
          description="可重新下载；只有服务端确认传输完成后，永久删除按钮才会启用。"
          action={(
            <Button icon={<ReloadOutlined />} loading={downloadM.isPending} onClick={retryDownload}>
              重新下载
            </Button>
          )}
        />
      );
    }
    if (exportRow.status === 'downloaded') {
      return (
        <Alert
          type="success"
          showIcon
          icon={<CheckCircleOutlined />}
          message="服务端已完整发送 ZIP"
          description="请先打开本地 ZIP 核对文件，再点击“已保存，删除服务器文件”。"
        />
      );
    }
    return (
      <Alert
        type="info"
        showIcon
        message="浏览器正在接收 ZIP"
        description="关闭窗口或下载失败都不会删除服务器文件。下载越大，完成状态更新所需时间越长。"
      />
    );
  })();
  const hasAvailableCategory = previewQ.data?.categories.some(
    (category) => category.available,
  ) ?? false;

  return (
    <>
      <Tooltip title={disabled ? disabledReason ?? '拆板任务执行期间暂不能导出或清理文件' : undefined}>
        <span>
          <Button
            icon={<DownloadOutlined />}
            disabled={disabled}
            onClick={show}
          >
            分批导出并清理
          </Button>
        </span>
      </Tooltip>
      <Modal
        open={open}
        title="分批导出并释放服务器空间"
        width={660}
        maskClosable={false}
        closable={!purgeM.isPending}
        onCancel={closeAndRetain}
        footer={exportRow ? (
          <Space wrap>
            <Button onClick={closeAndRetain} disabled={purgeM.isPending}>
              暂不删除
            </Button>
            {exportRow.status === 'download_failed' && (
              <Button icon={<ReloadOutlined />} loading={downloadM.isPending} onClick={retryDownload}>
                重新下载
              </Button>
            )}
            <Button
              danger
              type="primary"
              icon={<DeleteOutlined />}
              disabled={exportRow.status !== 'downloaded' || downloadM.isPending}
              loading={purgeM.isPending}
              onClick={confirmPurge}
            >
              已保存，删除服务器文件
            </Button>
          </Space>
        ) : (
          <Space>
            <Button onClick={closeAndRetain}>取消</Button>
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              loading={createM.isPending}
              disabled={!selected.length || previewQ.isLoading || previewQ.isError}
              onClick={() => createM.mutate()}
            >
              生成并下载 ZIP
            </Button>
          </Space>
        )}
      >
        {!exportRow && (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message="选择要导出的四类数据"
              description="ZIP 只使用下列固定一级目录；目录内沿用系统已登记的原文件名，不改名、不翻译、不加前后缀。"
            />
            {previewQ.isLoading && <Spin tip="正在统计可导出文件" />}
            {previewQ.isError && (
              <ApiErrorAlert
                title="可导出文件统计失败"
                error={previewQ.error}
                fallback="可导出文件统计失败"
                retryLabel="重新检查"
                retryLoading={previewQ.isFetching}
                onRetry={() => previewQ.refetch()}
              />
            )}
            {previewQ.data && !previewQ.isFetching && !hasAvailableCategory && (
              <Alert
                type="warning"
                showIcon
                message="当前没有可导出的文件"
                description="当前流程没有仍可用的分类后 DXF、拆板 DXF 或 Excel 文件。若此前已执行清理，这是正常结果。"
              />
            )}
            {previewQ.data && !previewQ.isFetching && (
              <Checkbox.Group
                className="workflow-batch-export-options"
                value={selected}
                onChange={(values) => setSelected(
                  values as WorkflowExportCategory[],
                )}
              >
                {previewQ.data.categories.map((category) => (
                  <Checkbox
                    className={`workflow-batch-export-option${category.available ? '' : ' is-disabled'}`}
                    key={category.key}
                    value={category.key}
                    disabled={!category.available}
                  >
                    <span className="workflow-batch-export-option-copy">
                      <Typography.Text strong>{category.label}</Typography.Text>
                      <Typography.Text type="secondary">
                        {category.file_count} 个文件 · {fmtSize(category.size_bytes)}
                      </Typography.Text>
                    </span>
                  </Checkbox>
                ))}
              </Checkbox.Group>
            )}
          </Space>
        )}
        {exportRow && (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            <div className="workflow-batch-export-summary">
              <div>
                <small>文件数量</small>
                <strong>{exportRow.file_count}</strong>
              </div>
              <div>
                <small>源文件总大小</small>
                <strong>{fmtSize(exportRow.source_size_bytes)}</strong>
              </div>
              <div>
                <small>本地文件名</small>
                <strong>{exportRow.filename}</strong>
              </div>
            </div>
            {downloadStatus}
            {downloadProgress && (
              <CancellableDownloadProgress
                label="分批图纸下载"
                progress={downloadProgress}
                active={downloadCtrl.active}
                onCancel={() => {
                  downloadCtrl.cancel();
                  setDownloadProgress(null);
                }}
              />
            )}
          </Space>
        )}
      </Modal>
    </>
  );
}
