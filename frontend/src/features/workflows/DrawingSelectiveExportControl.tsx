import { useEffect, useState } from 'react';
import { DownloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
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
import {
  ApiErrorAlert,
  CancellableDownloadProgress,
  fmtDateTime,
  fmtSize,
} from '../../shared/components';
import type {
  DrawingSelectiveExport,
  DrawingSelectiveExportCategory,
} from './workflow';
import {
  createDrawingSelectiveExport,
  getDrawingSelectiveExportPreview,
  downloadDrawingSelectiveExport,
} from './workflows.api';

export function DrawingSelectiveExportControl({
  workflowId,
  runId,
  disabled = false,
  disabledReason,
}: {
  workflowId: number;
  runId?: number;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const { message } = App.useApp();
  const downloadCtrl = useDownload();
  const [open, setOpen] = useState(false);
  const [selectionInitialized, setSelectionInitialized] = useState(false);
  const [selected, setSelected] = useState<DrawingSelectiveExportCategory[]>([]);
  const [prepared, setPrepared] = useState<DrawingSelectiveExport | null>(null);
  const [downloadProgress, setDownloadProgress] = useState<TransferProgress | null>(null);

  const previewQ = useQuery({
    queryKey: ['drawing-selective-export-preview', workflowId, runId],
    queryFn: () => getDrawingSelectiveExportPreview(workflowId, runId!),
    enabled: open && runId !== undefined,
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

  const downloadM = useMutation({
    mutationFn: (next: DrawingSelectiveExport) => {
      const handle = downloadCtrl.start();
      return downloadDrawingSelectiveExport(next, setDownloadProgress, handle.signal)
        .finally(handle.finish);
    },
    onSuccess: (_data, next) => {
      message.success(`已下载 ${next.file_count} 个 DXF`);
    },
    onError: (error) => {
      const result = describeDownloadError(error, '选择导出下载失败');
      if (result.cancelled) {
        message.info('下载已取消');
      } else {
        message.error(result.message);
      }
    },
  });

  const createM = useMutation({
    mutationFn: () => {
      if (runId === undefined) throw new Error('当前拆板批次尚未生成');
      return createDrawingSelectiveExport(workflowId, runId, selected);
    },
    onSuccess: (next) => {
      setPrepared(next);
      setDownloadProgress(null);
      downloadM.mutate(next);
    },
    onError: (error) => message.error(
      describeApiError(error, '选择导出创建失败'),
    ),
  });

  const show = () => {
    setPrepared(null);
    setSelectionInitialized(false);
    setSelected([]);
    setDownloadProgress(null);
    setOpen(true);
  };

  const close = () => {
    if (createM.isPending) return;
    if (downloadM.isPending) downloadCtrl.cancel();
    setOpen(false);
    setPrepared(null);
    setSelectionInitialized(false);
    setSelected([]);
  };
  const hasAvailableCategory = previewQ.data?.categories.some(
    (category) => category.available,
  ) ?? false;
  const buttonDisabled = disabled || runId === undefined;

  return (
    <>
      <Tooltip title={buttonDisabled ? disabledReason ?? '当前拆板批次尚未形成可导出的分类结果' : undefined}>
        <span>
          <Button
            icon={<DownloadOutlined />}
            disabled={buttonDisabled}
            onClick={show}
          >
            分类图纸导出
          </Button>
        </span>
      </Tooltip>
      <Modal
        open={open}
        title="选择要导出的图纸"
        width={620}
        maskClosable={false}
        closable={!createM.isPending}
        onCancel={close}
        footer={prepared ? (
          <Space wrap>
            <Button onClick={close} disabled={createM.isPending}>关闭</Button>
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              loading={downloadM.isPending}
              onClick={() => {
                setDownloadProgress(null);
                downloadM.mutate(prepared);
              }}
            >
              重新下载
            </Button>
          </Space>
        ) : (
          <Space>
            <Button onClick={close} disabled={createM.isPending}>取消</Button>
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              loading={createM.isPending}
              disabled={!selected.length || previewQ.isLoading || previewQ.isFetching || previewQ.isError}
              onClick={() => createM.mutate()}
            >
              下载所选 DXF
            </Button>
          </Space>
        )}
      >
        {!prepared ? (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="可同时勾选多个类别"
            description="ZIP 直接流式下载到本地，不生成服务器临时压缩包；文件沿用系统已登记的原文件名，下载后不会删除服务器文件。"
          />
          {previewQ.isLoading && <Spin tip="正在统计可导出图纸" />}
          {previewQ.isError && (
            <ApiErrorAlert
              title="可导出图纸统计失败"
              error={previewQ.error}
              fallback="分类图纸统计失败"
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
              description="当前拆板批次没有符合未通过 BH、未通过 BOX、PL 或其他类别的可用源 DXF。请刷新批次状态；若文件已被清理，请联系管理员。"
            />
          )}
          {previewQ.data && !previewQ.isFetching && (
            <Checkbox.Group
              className="workflow-batch-export-options"
              value={selected}
              onChange={(values) => setSelected(
                values as DrawingSelectiveExportCategory[],
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
        ) : (
          <Space orientation="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              type="success"
              showIcon
              message={downloadM.isPending ? '正在下载分类图纸 ZIP' : '分类图纸 ZIP 已准备'}
              description={`${prepared.file_count} 个 DXF；下载失败可在凭据有效期内重试。`}
            />
            <div className="workflow-batch-export-summary">
              <div>
                <small>图纸数量</small>
                <strong>{prepared.file_count} 个 DXF</strong>
              </div>
              <div>
                <small>源文件总大小</small>
                <strong>{fmtSize(prepared.source_size_bytes)}</strong>
              </div>
              <div>
                <small>下载文件名</small>
                <strong title={prepared.filename}>{prepared.filename}</strong>
              </div>
            </div>
            {downloadProgress && (
              <CancellableDownloadProgress
                label="分类图纸下载"
                progress={downloadProgress}
                active={downloadCtrl.active}
                onCancel={() => {
                  downloadCtrl.cancel();
                  setDownloadProgress(null);
                }}
              />
            )}
            <Typography.Text type="secondary">
              下载凭据有效至 {fmtDateTime(prepared.token_expires_at)}；下载不会删除服务器文件。
            </Typography.Text>
          </Space>
        )}
      </Modal>
    </>
  );
}
