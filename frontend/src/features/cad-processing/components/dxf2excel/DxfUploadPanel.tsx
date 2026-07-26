import { useRef, useState } from 'react';
import { Button, message, Typography } from 'antd';
import { FileZipOutlined, FolderOpenOutlined } from '@ant-design/icons';

import {
  limitFolderUploadFiles,
  MAX_FOLDER_FILES,
  uploadFile,
  uploadFolder,
  uploadZip,
} from '../../../files';
import { describeApiError, type TransferProgress } from '../../../../shared/api';
import { TransferProgressBar } from '../../../../shared/components';

export interface DxfUploadPanelProps {
  onUploaded: () => void;
}

export function DxfUploadPanel({ onUploaded }: DxfUploadPanelProps) {
  const folderInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [transferProgress, setTransferProgress] = useState<{
    label: string;
    progress: TransferProgress;
  } | null>(null);

  return (
    <div style={{ background: '#fafcff', border: '1px solid #e8ecf1', borderRadius: 10, padding: '12px 16px', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
      <input
        ref={folderInputRef}
        type="file"
        /* @ts-expect-error webkitdirectory */
        webkitdirectory=""
        multiple
        style={{ display: 'none' }}
        onChange={async (event) => {
          const raw = event.target.files;
          if (raw && raw.length > 0) {
            setBusy(true);
            setTransferProgress(null);
            const selected = Array.from(raw);
            const limited = limitFolderUploadFiles(selected);
            const files = limited.files;
            if (limited.omittedCount > 0) {
              message.error(
                `文件夹包含 ${selected.length} 个文件，超过上限；仅取前 ${MAX_FOLDER_FILES} 个文件上传，后 ${limited.omittedCount} 个文件已忽略。`,
              );
            }
            const firstPath = (files[0] as { webkitRelativePath?: string }).webkitRelativePath || '';
            const folderName = firstPath.split('/')[0] || `导入_${Date.now()}`;
            try {
              const result = await uploadFolder(files, folderName, {
                fileExt: '.dxf',
                onFile: (file, batchName, onProgress) => (
                  uploadFile(file, batchName, undefined, onProgress)
                ),
                onTransferProgress: (progress) => setTransferProgress({
                  label: 'DXF 文件夹上传',
                  progress,
                }),
              });
              if (result.success > 0) {
                message.success(`已导入 ${result.success}/${result.total} 个文件到 "${folderName}"`);
                if (result.failures.length > 0) {
                  const examples = result.failures.slice(0, 3)
                    .map((failure) => `${failure.file_name}: ${failure.reason}`)
                    .join('；');
                  message.warning(`部分文件上传失败：${examples}`, 10);
                }
                onUploaded();
              } else if (result.total > 0) {
                const examples = result.failures.slice(0, 3)
                  .map((failure) => `${failure.file_name}: ${failure.reason}`)
                  .join('；');
                message.error(`全部 ${result.total} 个文件上传失败${examples ? `：${examples}` : ''}`, 10);
              } else {
                message.warning('文件夹中没有 .dxf 文件');
              }
            } catch (error) {
              message.error(describeApiError(error, '文件夹上传失败'));
            } finally {
              setBusy(false);
              event.target.value = '';
            }
          }
        }}
      />
      <Button
        icon={<FolderOpenOutlined />}
        loading={busy}
        disabled={busy}
        onClick={() => folderInputRef.current?.click()}
        style={{ borderColor: '#722ed1', color: '#722ed1', fontWeight: 500 }}
      >
        上传文件夹
      </Button>
      <input
        ref={zipInputRef}
        type="file"
        accept=".zip"
        style={{ display: 'none' }}
        onChange={async (event) => {
          const file = event.target.files?.[0];
          if (!file) return;
          setBusy(true);
          setTransferProgress(null);
          try {
            const result = await uploadZip(file, '.dxf', (progress) => {
              setTransferProgress({ label: 'DXF 压缩包上传', progress });
            });
            if (result.success_count > 0) {
              message.success(`已解压 ${result.success_count}/${result.success_count + result.skipped_count} 个文件到 "${result.batch_name}"`);
              onUploaded();
            } else {
              message.warning('压缩包中没有 .dxf 文件');
            }
          } catch (error) {
            message.error(describeApiError(error, '压缩包解压失败'));
          } finally {
            setBusy(false);
            event.target.value = '';
          }
        }}
      />
      <Button
        icon={<FileZipOutlined />}
        loading={busy}
        disabled={busy}
        onClick={() => zipInputRef.current?.click()}
        style={{ borderColor: '#eb2f96', color: '#eb2f96', fontWeight: 500 }}
      >
        上传压缩包
      </Button>
      <Typography.Text type="secondary">
        支持 .dxf / .zip 格式，单文件最大 512 MB，每个文件夹将生成一个 Excel
      </Typography.Text>
      {transferProgress && (
        <TransferProgressBar
          label={transferProgress.label}
          progress={transferProgress.progress}
        />
      )}
    </div>
  );
}
