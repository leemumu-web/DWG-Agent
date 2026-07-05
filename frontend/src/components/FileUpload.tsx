import { useRef, useState } from 'react';
import { CloudUploadOutlined, LoadingOutlined } from '@ant-design/icons';
import { App, Card, Typography, Spin } from 'antd';
import { uploadDwgAndConvert } from '../api/files.api';

const { Text } = Typography;

export function FileUpload({ onUploaded }: { onUploaded?: () => void }) {
  const { message } = App.useApp();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [currentFile, setCurrentFile] = useState('');

  const handleFiles = async (files: FileList | File[]) => {
    if (uploading) return;
    setUploading(true);
    const list = Array.from(files);
    for (const f of list) {
      setCurrentFile(f.name);
      try {
        console.log('[FileUpload] processing:', f.name, f.size, f.type);
        await uploadDwgAndConvert(f);
        message.success(`${f.name} 上传成功`);
        onUploaded?.();
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error('[FileUpload] error:', msg, err);
        message.error(msg || `${f.name} 上传失败`);
      }
    }
    setCurrentFile('');
    setUploading(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer.files.length > 0) {
      handleFiles(e.dataTransfer.files);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleClick = () => {
    if (!uploading) inputRef.current?.click();
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      handleFiles(e.target.files);
      e.target.value = '';
    }
  };

  return (
    <Card style={{ marginBottom: 20, borderRadius: 12, overflow: 'hidden' }} styles={{ body: { padding: 0 } }}>
      <input
        ref={inputRef}
        type="file"
        accept=".dwg"
        multiple
        disabled={uploading}
        style={{ display: 'none' }}
        onChange={handleInputChange}
      />
      <div
        onClick={handleClick}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        style={{
          background: uploading
            ? 'linear-gradient(180deg, #f5f8fc 0%, #eaf0f8 100%)'
            : 'linear-gradient(180deg, #fafcff 0%, #f0f4fa 100%)',
          border: uploading ? '2px dashed #91caff' : '2px dashed #c8d6e5',
          borderRadius: 12,
          padding: '28px 24px',
          cursor: uploading ? 'default' : 'pointer',
          transition: 'all .3s',
          textAlign: 'center',
          userSelect: 'none',
          opacity: uploading ? 0.85 : 1,
        }}
        className="file-upload-zone"
      >
        {uploading ? (
          <>
            <Spin indicator={<LoadingOutlined style={{ fontSize: 36, color: '#1677ff' }} spin />} />
            <p style={{ fontSize: 15, fontWeight: 500, color: '#1f1f1f', margin: '10px 0 4px' }}>
              正在上传: {currentFile}
            </p>
            <Text type="secondary" style={{ fontSize: 13 }}>
              上传完成后将自动开始 DWG→DXF 转换
            </Text>
          </>
        ) : (
          <>
            <CloudUploadOutlined style={{ fontSize: 36, color: '#1677ff', marginBottom: 10 }} />
            <p style={{ fontSize: 15, fontWeight: 500, color: '#1f1f1f', margin: '0 0 4px' }}>
              点击或拖拽 DWG 文件到此区域
            </p>
            <Text type="secondary" style={{ fontSize: 13 }}>
              支持批量上传 · 自动 DWG→DXF 转换 · 单文件最大 512 MB
            </Text>
          </>
        )}
      </div>
    </Card>
  );
}
