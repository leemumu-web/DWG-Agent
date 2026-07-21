import { useRef, useState } from 'react';
import { Button, Progress, Tag, Typography, message } from 'antd';
import { FileZipOutlined, FolderOpenOutlined } from '@ant-design/icons';

import { FileUpload, uploadFile, uploadFolder, uploadZip, type StoredFile } from '../../../files';
import { createConversionBatches, type ConversionBatchSubmission } from '../../../jobs';

export type ConversionOperation =
  | 'file-upload'
  | 'folder-upload'
  | 'zip-upload'
  | 'batch-package'
  | 'batch-delete'
  | null;

function reportSubmission(prefix: string, submission: ConversionBatchSubmission): void {
  if (submission.unsubmittedFileIds.length > 0) {
    message.warning(
      `${prefix}；已提交 ${submission.submittedFileIds.length} 个，待补交 ${submission.unsubmittedFileIds.length} 个`,
    );
    return;
  }
  message.success(`${prefix}；已提交 ${submission.submittedJobs.length} 个转换任务`);
}

export interface ConversionUploadPanelProps {
  selectedBatch: string | null;
  acceptExt: string;
  fileExt: string;
  tagPending: string;
  uploadHint: string;
  taskType: string;
  operation: ConversionOperation;
  onOperationChange: (operation: ConversionOperation) => void;
  onUploaded: () => void;
}

export function ConversionUploadPanel({
  selectedBatch,
  acceptExt,
  fileExt,
  tagPending,
  uploadHint,
  taskType,
  operation,
  onOperationChange,
  onUploaded,
}: ConversionUploadPanelProps) {
  const folderInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [uploadProgress, setUploadProgress] = useState<{ processed: number; total: number } | null>(null);

  return (
    <div className="upload-toolbar">
      <input
        ref={folderInputRef}
        type="file"
        /* @ts-expect-error webkitdirectory */
        webkitdirectory=""
        multiple
        disabled={operation !== null}
        style={{ display: 'none' }}
        onChange={async (event) => {
          const raw = event.target.files;
          if (raw && raw.length > 0) {
            onOperationChange('folder-upload');
            const files = Array.from(raw);
            const firstPath = (files[0] as { webkitRelativePath?: string }).webkitRelativePath || '';
            const folderName = selectedBatch || firstPath.split('/')[0] || `导入_${Date.now()}`;
            const matchedCount = files.filter((file) => file.name.toLowerCase().endsWith(acceptExt)).length;
            setUploadProgress({ processed: 0, total: matchedCount });
            try {
              const result = await uploadFolder(files, folderName, {
                fileExt: acceptExt,
                concurrency: 4,
                onFile: (file: File, batchName: string) => uploadFile(file, batchName),
                onProgress: (processed, total) => setUploadProgress({ processed, total }),
              });
              const uploaded = result.results as StoredFile[];
              if (uploaded.length > 0) {
                const submission = await createConversionBatches(taskType, uploaded.map((file) => file.id));
                reportSubmission(`已上传 ${result.success}/${result.total} 个文件`, submission);
                if (result.failures.length > 0) {
                  const examples = result.failures.slice(0, 3)
                    .map((failure) => `${failure.file_name}: ${failure.reason}`)
                    .join('；');
                  const remaining = result.failures.length > 3 ? `；另有 ${result.failures.length - 3} 个失败` : '';
                  message.warning(`部分文件上传失败：${examples}${remaining}`, 10);
                }
                onUploaded();
              } else if (result.total > 0) {
                const examples = result.failures.slice(0, 3)
                  .map((failure) => `${failure.file_name}: ${failure.reason}`)
                  .join('；');
                message.error(`全部 ${result.total} 个文件上传失败${examples ? `：${examples}` : ''}`, 10);
              } else {
                message.warning(`文件夹中没有 ${fileExt} 文件`);
              }
            } catch (error) {
              message.error(error instanceof Error ? error.message : '文件夹导入失败');
            } finally {
              setUploadProgress(null);
              onOperationChange(null);
            }
            event.target.value = '';
          }
        }}
      />
      <FileUpload
        onUploaded={onUploaded}
        batchName={selectedBatch ?? undefined}
        acceptExt={acceptExt}
        label={`上传 ${tagPending} 文件`}
        disabled={operation !== null}
        onBusyChange={(busy) => onOperationChange(busy ? 'file-upload' : null)}
        uploadFn={async (file: File, batchName?: string) => {
          const stored = await uploadFile(file, batchName);
          const submission = await createConversionBatches(taskType, [stored.id]);
          if (submission.unsubmittedFileIds.length > 0) {
            throw new Error('文件已上传，但转换任务未提交；请使用“提交/重试”补交');
          }
          return submission;
        }}
      />
      <Button
        icon={<FolderOpenOutlined />}
        onClick={() => { if (!operation) folderInputRef.current?.click(); }}
        loading={operation === 'folder-upload'}
        disabled={operation !== null}
        style={{ borderColor: '#722ed1', color: '#722ed1', fontWeight: 500 }}
      >
        上传文件夹
      </Button>
      <input
        ref={zipInputRef}
        type="file"
        accept=".zip"
        disabled={operation !== null}
        style={{ display: 'none' }}
        onChange={async (event) => {
          const file = event.target.files?.[0];
          if (!file) return;
          onOperationChange('zip-upload');
          try {
            const result = await uploadZip(file, acceptExt);
            if (result.success_count > 0) {
              const submission = await createConversionBatches(
                taskType,
                result.files.map((stored) => stored.id),
              );
              reportSubmission(
                `已从压缩包上传 ${result.success_count}/${result.success_count + result.skipped_count} 个文件`,
                submission,
              );
              onUploaded();
            } else {
              message.warning(`压缩包中没有 ${acceptExt} 文件`);
            }
          } catch (error) {
            message.error(error instanceof Error ? error.message : '解压失败');
          } finally {
            onOperationChange(null);
          }
          event.target.value = '';
        }}
      />
      <Button
        icon={<FileZipOutlined />}
        onClick={() => { if (!operation) zipInputRef.current?.click(); }}
        loading={operation === 'zip-upload'}
        disabled={operation !== null}
        style={{ borderColor: '#eb2f96', color: '#eb2f96', fontWeight: 500 }}
      >
        上传压缩包
      </Button>
      <Typography.Text type="secondary" className="upload-toolbar-hint">
        支持 {acceptExt} / .zip 格式，单文件最大 512 MB，{uploadHint}
      </Typography.Text>
      {uploadProgress && uploadProgress.total > 0 && (
        <div style={{ minWidth: 180 }}>
          <Progress
            percent={Math.round((uploadProgress.processed / uploadProgress.total) * 100)}
            size="small"
            format={() => `${uploadProgress.processed}/${uploadProgress.total}`}
          />
        </div>
      )}
      {selectedBatch && <Tag color="purple" style={{ marginLeft: 'auto' }}>当前：{selectedBatch}</Tag>}
    </div>
  );
}
