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
  Typography,
} from 'antd';
import { describeApiError } from '../../shared/api';
import { fmtSize } from '../../shared/components';
import type {
  WorkflowBatchExport,
  WorkflowExportCategory,
} from './workflow';
import {
  createWorkflowBatchExport,
  getWorkflowBatchExport,
  getWorkflowBatchExportPreview,
  purgeWorkflowBatchExport,
  startNativeWorkflowBatchExportDownload,
} from './workflows.api';

const ACTIVE_DOWNLOAD_STATUSES = new Set(['prepared', 'downloading']);

export function WorkflowBatchExportControl({
  workflowId,
  disabled = false,
  onPurged,
}: {
  workflowId: number;
  disabled?: boolean;
  onPurged: () => void;
}) {
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [selectionInitialized, setSelectionInitialized] = useState(false);
  const [selected, setSelected] = useState<WorkflowExportCategory[]>([]);
  const [createdExport, setCreatedExport] = useState<WorkflowBatchExport | null>(null);

  const previewQ = useQuery({
    queryKey: ['workflow-batch-export-preview', workflowId],
    queryFn: () => getWorkflowBatchExportPreview(workflowId),
    enabled: open && !createdExport,
    staleTime: 0,
  });

  useEffect(() => {
    if (!open || !previewQ.data || selectionInitialized) return;
    setSelected(
      previewQ.data.categories
        .filter((category) => category.available)
        .map((category) => category.key),
    );
    setSelectionInitialized(true);
  }, [open, previewQ.data, selectionInitialized]);

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

  const createM = useMutation({
    mutationFn: () => createWorkflowBatchExport(workflowId, selected),
    onSuccess: (next) => {
      setCreatedExport(next);
      queryClient.setQueryData(
        ['workflow-batch-export', workflowId, next.export_uid],
        next,
      );
      startNativeWorkflowBatchExportDownload(next);
      message.info('浏览器已开始接收分批导出 ZIP，请保存到本地');
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
    setOpen(true);
  };

  const closeAndRetain = () => {
    if (purgeM.isPending) return;
    setOpen(false);
    setCreatedExport(null);
    setSelectionInitialized(false);
    setSelected([]);
  };

  const retryDownload = () => {
    if (!exportRow) return;
    try {
      startNativeWorkflowBatchExportDownload(exportRow);
      message.info('已重新发起浏览器下载');
      setTimeout(() => {
        void statusQ.refetch();
      }, 500);
    } catch (error) {
      message.error(describeApiError(error, '重新下载失败'));
    }
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
        <Alert
          type="error"
          showIcon
          message="下载状态读取失败"
          description={describeApiError(statusQ.error, '请刷新状态后重试')}
          action={(
            <Button
              icon={<ReloadOutlined />}
              loading={statusQ.isFetching}
              onClick={() => statusQ.refetch()}
            >
              刷新状态
            </Button>
          )}
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
            <Button icon={<ReloadOutlined />} onClick={retryDownload}>
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

  return (
    <>
      <Button
        icon={<DownloadOutlined />}
        disabled={disabled}
        onClick={show}
      >
        分批导出
      </Button>
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
              <Button icon={<ReloadOutlined />} onClick={retryDownload}>
                重新下载
              </Button>
            )}
            <Button
              danger
              type="primary"
              icon={<DeleteOutlined />}
              disabled={exportRow.status !== 'downloaded'}
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
              <Alert
                type="error"
                showIcon
                message="可导出文件统计失败"
                description={describeApiError(previewQ.error, '请重试')}
                action={(
                  <Button
                    icon={<ReloadOutlined />}
                    loading={previewQ.isFetching}
                    onClick={() => previewQ.refetch()}
                  >
                    重试
                  </Button>
                )}
              />
            )}
            {previewQ.data && (
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
          </Space>
        )}
      </Modal>
    </>
  );
}
