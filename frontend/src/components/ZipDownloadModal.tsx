import { useState } from 'react';
import { Button, Checkbox, Input, Modal, Space, Tooltip, Typography, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { downloadZip } from '../api/files.api';

interface Props {
  open: boolean;
  fileIds: number[];
  fileCount: number;
  onClose: () => void;
  onDone: () => void;
}

export function ZipDownloadModal({ open, fileIds, fileCount, onClose, onDone }: Props) {
  const [folderName, setFolderName] = useState('图纸导出');
  const [dwg, setDwg] = useState(true);
  const [dxf, setDxf] = useState(true);
  const [loading, setLoading] = useState(false);

  const validFormat = dwg || dxf;
  const validName = folderName.trim().length > 0;
  const canDownload = validFormat && validName && fileIds.length > 0;

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
    try {
      await downloadZip(fileIds, formats, folderName.trim());
      message.success('打包下载完成');
      onDone();
      onClose();
    } catch (err) {
      message.error(err instanceof Error ? err.message : '打包下载失败');
      onClose();
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
            <Checkbox checked={dwg} onChange={(e) => setDwg(e.target.checked)}>
              包含 DWG 文件
            </Checkbox>
            <Checkbox checked={dxf} onChange={(e) => setDxf(e.target.checked)}>
              包含 DXF 文件
            </Checkbox>
          </Space>
        </div>
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
