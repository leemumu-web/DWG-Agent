import { App, Button, Upload } from 'antd';
import { FileExcelOutlined, PlayCircleOutlined } from '@ant-design/icons';

import { fmtSize } from '../../../shared/components';

interface ExcelFinalUploadActionsProps {
  file: File | null;
  maximumBytes?: number;
  submitting: boolean;
  requestKeyAvailable: boolean;
  onSelect: (file: File) => void;
  onRemove: () => void;
  onSubmit: () => void;
}

export function excelUploadSizeMessage(file: File, maximumBytes?: number): string | null {
  if (!maximumBytes || file.size <= maximumBytes) return null;
  return `所选 Excel 为 ${fmtSize(file.size)}，超过服务器允许的 ${fmtSize(maximumBytes)}。`;
}

export function ExcelFinalUploadActions({
  file,
  maximumBytes,
  submitting,
  requestKeyAvailable,
  onSelect,
  onRemove,
  onSubmit,
}: ExcelFinalUploadActionsProps) {
  const { message } = App.useApp();
  return (
    <div className="excel-final-ingest-actions">
      <Upload
        accept=".xlsx,.xls"
        maxCount={1}
        fileList={file ? [{ uid: 'selected', name: file.name, status: 'done' }] : []}
        beforeUpload={(nextFile) => {
          if (!/\.xlsx?$/i.test(nextFile.name)) {
            message.error('请选择 .xlsx 或 .xls 文件');
            return Upload.LIST_IGNORE;
          }
          const sizeMessage = excelUploadSizeMessage(nextFile, maximumBytes);
          if (sizeMessage) {
            message.error(sizeMessage);
            return Upload.LIST_IGNORE;
          }
          onSelect(nextFile);
          return false;
        }}
        onRemove={() => {
          onRemove();
          return true;
        }}
      >
        <Button icon={<FileExcelOutlined />}>选择 Excel</Button>
      </Upload>
      <Button
        type="primary"
        icon={<PlayCircleOutlined />}
        disabled={!file || !requestKeyAvailable}
        loading={submitting}
        onClick={onSubmit}
      >
        提交处理
      </Button>
    </div>
  );
}
