import { useEffect, useState } from 'react';
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
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
import type { DrawingSelectiveExportCategory } from './workflow';
import {
  createDrawingSelectiveExport,
  getDrawingSelectiveExportPreview,
  startNativeDrawingSelectiveExportDownload,
} from './workflows.api';

export function DrawingSelectiveExportControl({
  workflowId,
  runId,
  disabled = false,
}: {
  workflowId: number;
  runId?: number;
  disabled?: boolean;
}) {
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [selectionInitialized, setSelectionInitialized] = useState(false);
  const [selected, setSelected] = useState<DrawingSelectiveExportCategory[]>([]);

  const previewQ = useQuery({
    queryKey: ['drawing-selective-export-preview', workflowId, runId],
    queryFn: () => getDrawingSelectiveExportPreview(workflowId, runId!),
    enabled: open && runId !== undefined,
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

  const createM = useMutation({
    mutationFn: () => {
      if (runId === undefined) throw new Error('当前拆板批次尚未生成');
      return createDrawingSelectiveExport(workflowId, runId, selected);
    },
    onSuccess: (prepared) => {
      startNativeDrawingSelectiveExportDownload(prepared);
      message.info(`浏览器已开始接收 ${prepared.file_count} 个 DXF`);
      setOpen(false);
      setSelectionInitialized(false);
      setSelected([]);
    },
    onError: (error) => message.error(
      describeApiError(error, '选择导出创建失败'),
    ),
  });

  const show = () => {
    setSelectionInitialized(false);
    setSelected([]);
    setOpen(true);
  };

  const close = () => {
    if (createM.isPending) return;
    setOpen(false);
    setSelectionInitialized(false);
    setSelected([]);
  };

  return (
    <>
      <Button
        icon={<DownloadOutlined />}
        disabled={disabled || runId === undefined}
        onClick={show}
      >
        导出
      </Button>
      <Modal
        open={open}
        title="选择要导出的图纸"
        width={620}
        maskClosable={false}
        closable={!createM.isPending}
        onCancel={close}
        footer={(
          <Space>
            <Button onClick={close} disabled={createM.isPending}>取消</Button>
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              loading={createM.isPending}
              disabled={!selected.length || previewQ.isLoading || previewQ.isError}
              onClick={() => createM.mutate()}
            >
              下载所选 DXF
            </Button>
          </Space>
        )}
      >
        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="可同时勾选多个类别"
            description="ZIP 直接流式下载到本地，不生成服务器临时压缩包；文件沿用系统已登记的原文件名，下载后不会删除服务器文件。"
          />
          {previewQ.isLoading && <Spin tip="正在统计可导出图纸" />}
          {previewQ.isError && (
            <Alert
              type="error"
              showIcon
              message="可导出图纸统计失败"
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
      </Modal>
    </>
  );
}
