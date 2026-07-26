import { useState, useCallback, useMemo, useEffect, useRef, type FC } from 'react';
import {
  Modal, Table, Skeleton, Empty, Space, Typography, message,
  Tabs, Button, Badge,
} from 'antd';
import {
  FileExcelOutlined, DownloadOutlined, ReloadOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { downloadFile, fetchExcelPreview } from '../files';
import type { ExcelPreviewResponse } from '../files';
import { describeApiError } from '../../shared/api';
import {
  buildFastColumns,
  isSummaryRow,
} from './model/excelPreviewModel';

const { Text } = Typography;

interface ExcelPreviewProps {
  fileId: number | null;
  fileName?: string;
  open: boolean;
  onClose: () => void;
}

const ExcelPreview: FC<ExcelPreviewProps> = ({ fileId, fileName, open, onClose }) => {
  const [data, setData] = useState<ExcelPreviewResponse | null>(null);
  const [fastLoading, setFastLoading] = useState(false);
  const [activeSheet, setActiveSheet] = useState('');
  const fastLoadedFileId = useRef<number | null>(null);

  useEffect(() => {
    if (!open) {
      setData(null);
      setActiveSheet('');
      fastLoadedFileId.current = null;
    }
  }, [open]);

  const loadFast = useCallback(async (fid: number, sheet?: string) => {
    setFastLoading(true);
    try {
      const result = await fetchExcelPreview(fid, sheet);
      setData(result);
      fastLoadedFileId.current = fid;
      if (!sheet && result.sheets.length > 0) {
        setActiveSheet(result.sheet || result.sheets[0]);
      }
    } catch (err) {
      message.error(describeApiError(err, '加载预览失败'));
    } finally {
      setFastLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open && fileId !== null && fastLoadedFileId.current !== fileId) {
      setData(null);
      setActiveSheet('');
      loadFast(fileId);
    }
  }, [open, fileId, loadFast]);

  const handleSheetChange = useCallback((key: string) => {
    setActiveSheet(key);
    if (fileId !== null) loadFast(fileId, key);
  }, [fileId, loadFast]);

  const columns = useMemo(
    () => data?.headers
      ? buildFastColumns(data.headers, data.rows as Record<string, unknown>[])
      : [],
    [data],
  );

  const rowClassName = useCallback(
    (record: Record<string, unknown>) => isSummaryRow(record) ? 'row-summary' : '',
    [],
  );

  const sheetCount = data?.sheets.length || 0;
  const totalRows = data?.total_rows || 0;
  const displayName = data?.file || fileName;
  const sheetItems = useMemo(
    () => (data?.sheets || []).map((sheet) => ({
      key: sheet,
      label: (
        <Space size={2}>
          <TableOutlined style={{ fontSize: 11 }} />
          {sheet}
        </Space>
      ),
    })),
    [data],
  );

  const handleDownload = useCallback(async () => {
    if (fileId === null || !fileName) return;
    try {
      await downloadFile(fileId, fileName);
    } catch (err) {
      message.error(describeApiError(err, '下载失败'));
    }
  }, [fileId, fileName]);

  const handleRefresh = useCallback(() => {
    if (fileId !== null) loadFast(fileId, activeSheet || undefined);
  }, [fileId, activeSheet, loadFast]);

  return (
    <Modal
      title={
        <Space size={12}>
          <FileExcelOutlined style={{ color: '#52c41a', fontSize: 16 }} />
          <Text strong style={{ fontSize: 15 }}>{displayName || 'Excel 预览'}</Text>
          {totalRows > 0 && (
            <Badge
              count={`${totalRows.toLocaleString()} 行`}
              style={{ backgroundColor: '#1677ff' }}
            />
          )}
          {sheetCount > 1 && (
            <Badge
              count={`${sheetCount} 表`}
              style={{ backgroundColor: '#52c41a' }}
            />
          )}
        </Space>
      }
      open={open}
      onCancel={onClose}
      width="94vw"
      style={{ top: 12 }}
      styles={{ body: { padding: '12px 16px' } }}
      footer={
        <Space>
          <Button
            icon={<ReloadOutlined spin={fastLoading} />}
            onClick={handleRefresh}
            disabled={fastLoading}
            size="middle"
          >
            刷新
          </Button>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            onClick={handleDownload}
            size="middle"
          >
            下载 {fileName || ''}
          </Button>
          <Button onClick={onClose} size="middle">关闭</Button>
        </Space>
      }
      destroyOnHidden
    >
      {data && !fastLoading && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            minHeight: 30,
            marginBottom: 8,
            padding: '4px 8px',
            background: '#fafcff',
            border: '1px solid #e8ecf1',
            borderRadius: 8,
          }}
        >
          <Text type="secondary" style={{ fontSize: 11 }}>
            {activeSheet}
          </Text>
        </div>
      )}

      {fastLoading && (
        <div style={{ padding: '8px 0' }}>
          <Skeleton active paragraph={{ rows: 6 }} title={{ width: '40%' }} />
        </div>
      )}

      {data && !fastLoading && (
        <div>
          {sheetCount > 1 && (
            <Tabs
              activeKey={activeSheet}
              onChange={handleSheetChange}
              size="small"
              type="card"
              items={sheetItems}
              style={{ marginBottom: 6 }}
            />
          )}
          <Table
            size="small"
            dataSource={data.rows.map((row, index) => ({ ...row, _rowIdx: index }))}
            rowKey="_rowIdx"
            columns={columns}
            rowClassName={rowClassName}
            scroll={{ x: 'max-content', y: 'calc(94vh - 260px)' }}
            pagination={false}
            bordered
            sticky={{ offsetHeader: 0 }}
          />
        </div>
      )}

      {!data && !fastLoading && (
        <Empty description="无法加载预览数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </Modal>
  );
};

export default ExcelPreview;
