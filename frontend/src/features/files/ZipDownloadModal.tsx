import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Input, Modal, Space, Tooltip, Typography, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { describeApiError, type TransferProgress } from '../../shared/api';
import { TransferProgressBar } from '../../shared/components';
import {
  downloadZip,
  previewZip,
  type ZipAvailabilityPreview,
  type ZipFormatAvailability,
} from './files.api';

interface Props {
  open: boolean;
  fileIds: number[];
  fileCount: number;
  sourceFormat: 'dwg' | 'dxf';
  onClose: () => void;
  onDone: () => void;
}

export function ZipDownloadModal({
  open,
  fileIds,
  fileCount,
  sourceFormat,
  onClose,
  onDone,
}: Props) {
  const [folderName, setFolderName] = useState('图纸导出');
  const [dwg, setDwg] = useState(true);
  const [dxf, setDxf] = useState(false);
  const [loading, setLoading] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [preview, setPreview] = useState<ZipAvailabilityPreview | null>(null);
  const [downloadProgress, setDownloadProgress] = useState<TransferProgress | null>(null);

  const refreshPreview = useCallback(async (resetSelection: boolean) => {
    if (fileIds.length === 0) {
      setPreview(null);
      setPreviewError('没有可打包的文件');
      return;
    }
    if (resetSelection) {
      setDwg(sourceFormat === 'dwg');
      setDxf(sourceFormat === 'dxf');
    }
    setPreviewLoading(true);
    setPreviewError('');
    try {
      const nextPreview = await previewZip(
        fileIds,
        ['dwg', 'dxf'],
        folderName.trim() || '图纸导出',
      );
      setPreview(nextPreview);
      const complete = new Map(nextPreview.formats.map((item) => [item.format, item.complete]));
      if (!complete.get('dwg')) setDwg(false);
      if (!complete.get('dxf')) setDxf(false);
    } catch (error) {
      setPreview(null);
      setPreviewError(describeApiError(error, '无法检查打包内容'));
    } finally {
      setPreviewLoading(false);
    }
  }, [fileIds, folderName, sourceFormat]);

  useEffect(() => {
    if (!open) return;
    void refreshPreview(true);
    // Folder name is not part of availability. Re-check only when the modal,
    // selected files, or conversion direction changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, fileIds, sourceFormat]);

  const availability = (format: 'dwg' | 'dxf'): ZipFormatAvailability | undefined => (
    preview?.formats.find((item) => item.format === format)
  );
  const dwgAvailability = availability('dwg');
  const dxfAvailability = availability('dxf');
  const dwgUnavailable = previewLoading || !dwgAvailability?.complete;
  const dxfUnavailable = previewLoading || !dxfAvailability?.complete;

  const validFormat = dwg || dxf;
  const validName = folderName.trim().length > 0;
  const selectedFormatsComplete = (!dwg || dwgAvailability?.complete === true)
    && (!dxf || dxfAvailability?.complete === true);
  const canDownload = validFormat
    && validName
    && fileIds.length > 0
    && !previewLoading
    && !previewError
    && selectedFormatsComplete;

  const disabledReason = !validFormat
    ? '请至少选择一个下载格式（DWG 或 DXF）'
    : !validName
      ? '请输入文件夹名称'
      : '';

  const handleDownload = async () => {
    if (!canDownload) return;
    const formats: string[] = [];
    if (dwg) formats.push('dwg');
    if (dxf) formats.push('dxf');
    setLoading(true);
    setDownloadProgress(null);
    try {
      await downloadZip(fileIds, formats, folderName.trim(), setDownloadProgress);
      message.success('打包下载完成');
      onDone();
      onClose();
    } catch (err) {
      message.error(describeApiError(err, '打包下载失败'));
      await refreshPreview(false);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title="打包下载"
      open={open}
      onCancel={onClose}
      width={400}
      footer={[
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Tooltip title={disabledReason} open={!canDownload ? undefined : false}>
          <Button
            key="download"
            type="primary"
            icon={<DownloadOutlined />}
            loading={loading}
            disabled={!canDownload}
            onClick={handleDownload}
          >
            开始下载
          </Button>
        </Tooltip>,
      ]}
      destroyOnHidden
    >
      <Space orientation="vertical" style={{ width: '100%' }} size="middle">
        <div>
          <Typography.Text strong>已选 {fileCount} 个文件</Typography.Text>
        </div>
        <div>
          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>
            导出文件夹名称
          </Typography.Text>
          <Input
            value={folderName}
            onChange={(e) => setFolderName(e.target.value)}
            placeholder="输入文件夹名称"
            maxLength={60}
            suffix={<Typography.Text type="secondary">.zip</Typography.Text>}
          />
        </div>
        <div>
          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>
            下载内容（至少选一项）
          </Typography.Text>
          <Space orientation="vertical">
            <Checkbox
              checked={dwg}
              disabled={dwgUnavailable}
              onChange={(e) => setDwg(e.target.checked)}
            >
              包含 DWG 文件
              <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                {previewLoading
                  ? '检查中'
                  : `DWG：可用 ${dwgAvailability?.available_count ?? 0} / 共 ${preview?.file_count ?? fileCount}`}
              </Typography.Text>
            </Checkbox>
            <Checkbox
              checked={dxf}
              disabled={dxfUnavailable}
              onChange={(e) => setDxf(e.target.checked)}
            >
              包含 DXF 文件
              <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                {previewLoading
                  ? '检查中'
                  : `DXF：可用 ${dxfAvailability?.available_count ?? 0} / 共 ${preview?.file_count ?? fileCount}`}
              </Typography.Text>
            </Checkbox>
          </Space>
        </div>
        {previewError && (
          <Alert
            type="error"
            showIcon
            message="打包内容检查失败"
            description={previewError}
            action={<Button size="small" onClick={() => void refreshPreview(false)}>重新检查</Button>}
          />
        )}
        {downloadProgress && (
          <TransferProgressBar label="图纸打包下载" progress={downloadProgress} />
        )}
        <div style={{ background: '#fafafa', borderRadius: 6, padding: '8px 12px' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            文件将打包为 <Typography.Text code style={{ fontSize: 12 }}>{folderName || '...'}.zip</Typography.Text>，
            内部结构：<br />
            {folderName || '...'}/<br />
            &nbsp;&nbsp;└─ xxx.dwg<br />
            {dxf && <>&nbsp;&nbsp;└─ xxx.dxf</>}
          </Typography.Text>
        </div>
      </Space>
    </Modal>
  );
}
