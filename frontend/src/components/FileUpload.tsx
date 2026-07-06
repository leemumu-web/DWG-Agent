import { useRef, useState } from 'react';
import { CloudUploadOutlined, LoadingOutlined } from '@ant-design/icons';
import { App, Typography, Spin, Progress } from 'antd';
import { uploadDwgAndConvert } from '../api/files.api';

const { Text } = Typography;

export function FileUpload({ onUploaded }: { onUploaded?: () => void }) {
  const { message } = App.useApp();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [currentFile, setCurrentFile] = useState('');
  const [currentIndex, setCurrentIndex] = useState(0);
  const [totalFiles, setTotalFiles] = useState(0);
  const [dragOver, setDragOver] = useState(false);

  const handleFiles = async (files: FileList | File[]) => {
    if (uploading) return;
    setUploading(true);
    const list = Array.from(files);
    setTotalFiles(list.length);
    for (let i = 0; i < list.length; i++) {
      const f = list[i];
      setCurrentFile(f.name);
      setCurrentIndex(i + 1);
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
    setCurrentIndex(0);
    setTotalFiles(0);
    setUploading(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      handleFiles(e.dataTransfer.files);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
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

  const batchHint = totalFiles > 1
    ? ` (${currentIndex}/${totalFiles})`
    : '';

  return (
    <div style={{ marginBottom: 20 }}>
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
        onDragLeave={handleDragLeave}
        style={{
          background: dragOver
            ? 'linear-gradient(180deg, #e6f4ff 0%, #bae0ff 100%)'
            : uploading
              ? 'linear-gradient(180deg, #f5f8fc 0%, #eaf0f8 100%)'
              : 'linear-gradient(180deg, #fafcff 0%, #f0f4fa 100%)',
          border: dragOver
            ? '2px dashed #1677ff'
            : uploading
              ? '2px dashed #91caff'
              : '2px dashed #c8d6e5',
          borderRadius: 12,
          padding: '48px 32px',
          cursor: uploading ? 'default' : 'pointer',
          transition: 'all .25s ease',
          textAlign: 'center' as const,
          userSelect: 'none' as const,
          opacity: uploading ? 0.85 : 1,
          minHeight: 160,
          display: 'flex',
          flexDirection: 'column' as const,
          alignItems: 'center',
          justifyContent: 'center',
        }}
        className="file-upload-zone"
      >
        {uploading ? (
          <>
            <Spin indicator={<LoadingOutlined style={{ fontSize: 40, color: '#1677ff' }} spin />} />
            <p style={{ fontSize: 16, fontWeight: 500, color: '#1f1f1f', margin: '12px 0 4px' }}>
              正在上传{batchHint}: {currentFile}
            </p>
            <Progress
              percent={totalFiles > 0 ? Math.round(((currentIndex - 1) / totalFiles) * 100) : undefined}
              status="active"
              strokeColor="#1677ff"
              style={{ maxWidth: 320, margin: '8px 0 0' }}
            />
            <Text type="secondary" style={{ fontSize: 13, marginTop: 8 }}>
              上传完成后将自动开始 DWG→DXF 转换
            </Text>
          </>
        ) : (
          <>
            <CloudUploadOutlined
              style={{
                fontSize: 48,
                color: dragOver ? '#1677ff' : '#8cadd9',
                marginBottom: 16,
                transition: 'color .25s ease',
              }}
            />
            <p style={{ fontSize: 16, fontWeight: 500, color: '#1f1f1f', margin: '0 0 6px' }}>
              点击或拖拽 DWG 文件到此区域
            </p>
            <Text type="secondary" style={{ fontSize: 13 }}>
              支持批量上传 · 自动 DWG→DXF 转换 · 单文件最大 512 MB
            </Text>
            <div style={{
              display: 'flex', gap: 16, marginTop: 16,
              color: '#8c8c8c', fontSize: 12,
            }}>
              <span>📄 支持 AutoCAD R2013–R2026 (.dwg)</span>
              <span>🔄 自动转换为 DXF R2018 格式</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
