import { useRef } from 'react';
import { CloudUploadOutlined } from '@ant-design/icons';
import { App, Card, Typography } from 'antd';
import { uploadDwgAndConvert } from '../api/files.api';

const { Text } = Typography;

export function FileUpload({ onUploaded }: { onUploaded?: () => void }) {
  const { message } = App.useApp();
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadingRef = useRef(false);

  const handleFiles = async (files: FileList | File[]) => {
    if (uploadingRef.current) return;
    uploadingRef.current = true;
    const list = Array.from(files);
    for (const f of list) {
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
    uploadingRef.current = false;
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
    inputRef.current?.click();
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      handleFiles(e.target.files);
      // Reset so the same file can be re-uploaded
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
        style={{ display: 'none' }}
        onChange={handleInputChange}
      />
      <div
        onClick={handleClick}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        style={{
          background: 'linear-gradient(180deg, #fafcff 0%, #f0f4fa 100%)',
          border: '2px dashed #c8d6e5',
          borderRadius: 12,
          padding: '28px 24px',
          cursor: 'pointer',
          transition: 'all .2s',
          textAlign: 'center',
          userSelect: 'none',
        }}
        className="file-upload-zone"
      >
        <CloudUploadOutlined style={{ fontSize: 36, color: '#1677ff', marginBottom: 10 }} />
        <p style={{ fontSize: 15, fontWeight: 500, color: '#1f1f1f', margin: '0 0 4px' }}>
          点击或拖拽 DWG 文件到此区域
        </p>
        <Text type="secondary" style={{ fontSize: 13 }}>
          支持批量上传 · 自动 DWG→DXF 转换 · 单文件最大 512 MB
        </Text>
      </div>
    </Card>
  );
}
