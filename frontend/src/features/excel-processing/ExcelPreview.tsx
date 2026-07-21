import { useState, useCallback, useMemo, useEffect, useRef, type FC } from 'react';
import {
  Modal, Table, Skeleton, Empty, Space, Typography, message,
  Tabs, Button, Tooltip, Badge, Segmented,
} from 'antd';
import {
  FileExcelOutlined, DownloadOutlined, ReloadOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { downloadFile, fetchExcelPreview, getFileDownloadUrl } from '../files';
import { apiClient } from '../../shared/api';
import type { ExcelPreviewResponse } from '../files';
import {
  buildFastColumns,
  buildLuckyColumns,
  buildLuckyTable,
  isSummaryRow,
  type LuckyExportJson,
  type PreviewMode,
} from './model/excelPreviewModel';

const { Text } = Typography;

// ── component ────────────────────────────────────────────────────────────────

interface ExcelPreviewProps {
  fileId: number | null;
  fileName?: string;
  open: boolean;
  onClose: () => void;
}

const ExcelPreview: FC<ExcelPreviewProps> = ({ fileId, fileName, open, onClose }) => {
  const [mode, setMode] = useState<PreviewMode>('fast');

  // ── Fast path ──────────────────────────────────────────────────────────
  const [data, setData] = useState<ExcelPreviewResponse | null>(null);
  const [fastLoading, setFastLoading] = useState(false);
  const [activeSheet, setActiveSheet] = useState('');

  // ── Enhanced path ──────────────────────────────────────────────────────
  const [luckyLoading, setLuckyLoading] = useState(false);
  const [luckyData, setLuckyData] = useState<LuckyExportJson | null>(null);
  const [luckySheet, setLuckySheet] = useState('');
  const [luckyError, setLuckyError] = useState<string | null>(null);
  const scriptLoadedRef = useRef(false);

  // Track which fileId is currently loaded in fast mode to avoid re-fetching
  const fastLoadedFileId = useRef<number | null>(null);

  // ── reset on close ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!open) {
      setData(null); setLuckyData(null); setMode('fast');
      setLuckyError(null); setActiveSheet(''); setLuckySheet('');
      fastLoadedFileId.current = null;
    }
  }, [open]);

  // ── fast: load backend JSON ────────────────────────────────────────────
  const loadFast = useCallback(async (fid: number, sheet?: string) => {
    setFastLoading(true);
    try {
      const result = await fetchExcelPreview(fid, sheet);
      setData(result);
      fastLoadedFileId.current = fid;
      if (!sheet && result.sheets.length > 0) setActiveSheet(result.sheet || result.sheets[0]);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载预览失败');
    } finally {
      setFastLoading(false);
    }
  }, []);

  // Auto-load fast preview when opened (once per fileId)
  useEffect(() => {
    if (open && fileId !== null && fastLoadedFileId.current !== fileId) {
      setData(null); setActiveSheet('');
      loadFast(fileId);
    }
  }, [open, fileId, loadFast]);

  const handleSheetChange = useCallback((key: string) => {
    setActiveSheet(key);
    if (fileId !== null) loadFast(fileId, key);
  }, [fileId, loadFast]);

  // ── enhanced: LuckyExcel ───────────────────────────────────────────────
  const enhancedAbortRef = useRef(false);

  const ensureLuckyExcel = useCallback((): Promise<void> => {
    return new Promise((resolve, reject) => {
      if (window.LuckyExcel) { resolve(); return; }
      if (scriptLoadedRef.current) {
        const check = setInterval(() => {
          if (enhancedAbortRef.current) { clearInterval(check); reject(new Error('aborted')); return; }
          if (window.LuckyExcel) { clearInterval(check); resolve(); }
        }, 100);
        return;
      }
      scriptLoadedRef.current = true;
      const script = document.createElement('script');
      script.src = '/luckyexcel.umd.js';
      script.onload = () => resolve();
      script.onerror = () => { scriptLoadedRef.current = false; reject(new Error('Failed to load LuckyExcel')); };
      document.head.appendChild(script);
    });
  }, []);

  const loadEnhanced = useCallback(async (fid: number) => {
    enhancedAbortRef.current = false;
    setLuckyLoading(true); setLuckyError(null);
    let blobUrl: string | null = null;
    try {
      await ensureLuckyExcel();
      if (enhancedAbortRef.current) return;
      // Get signed download URL, then fetch the file via authenticated API
      // client.  LuckyExcel's transformExcelToLuckyByUrl fetches directly
      // without auth headers, so we pre-download the blob and pass a local
      // blob: URL instead.
      const { url: downloadPath } = await getFileDownloadUrl(fid);
      if (enhancedAbortRef.current) return;
      const response = await apiClient.get(downloadPath, { responseType: 'arraybuffer' });
      if (enhancedAbortRef.current) return;
      const blob = new Blob([response.data], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      blobUrl = URL.createObjectURL(blob);
      const exportData = await new Promise<LuckyExportJson>((resolve, reject) => {
        window.LuckyExcel!.transformExcelToLuckyByUrl(blobUrl!, fileName || 'preview.xlsx', resolve, reject);
      });
      if (enhancedAbortRef.current) return;
      setLuckyData(exportData);
      if (exportData.sheets.length > 0) setLuckySheet(exportData.sheets[0].name);
    } catch (err) {
      if (enhancedAbortRef.current) return;
      setLuckyError(err instanceof Error ? err.message : '加载失败');
      message.error('增强预览加载失败，已切回快速预览');
      setMode('fast');
    } finally {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      setLuckyLoading(false);
    }
  }, [fileName, ensureLuckyExcel]);

  // Trigger enhanced load when mode switches
  useEffect(() => {
    if (mode === 'enhanced' && fileId !== null && !luckyData && !luckyLoading) {
      loadEnhanced(fileId);
    }
    if (mode !== 'enhanced') {
      enhancedAbortRef.current = true;
    }
  }, [mode, fileId, luckyData, luckyLoading, loadEnhanced]);

  // ── LuckyExcel → table data ────────────────────────────────────────────
  const luckyTable = useMemo(() => buildLuckyTable(luckyData, luckySheet), [luckyData, luckySheet]);

  // ── column builders ────────────────────────────────────────────────────

  /** Fast columns: content-aware widths from sampling actual data. */
  const fastCols = useMemo(
    () => data?.headers ? buildFastColumns(data.headers, data.rows as Record<string, unknown>[]) : [],
    [data],
  );

  /** Enhanced columns: LuckyExcel widths + merged cell spans. */
  const luckyCols = useMemo(() => buildLuckyColumns(luckyTable), [luckyTable]);

  const rowClassName = useCallback(
    (record: Record<string, unknown>) => isSummaryRow(record) ? 'row-summary' : '',
    [],
  );

  // ── current table state ────────────────────────────────────────────────
  const isEnhanced = mode === 'enhanced';
  const isLoading = isEnhanced ? luckyLoading : fastLoading;
  const hasData = isEnhanced ? !!luckyData : !!data;
  const sheetCount = isEnhanced ? (luckyData?.sheets.length || 0) : (data?.sheets.length || 0);
  const totalRows = isEnhanced
    ? luckyTable.rows.length
    : (data?.total_rows || 0);
  const currentSheetName = isEnhanced ? luckySheet : activeSheet;
  const displayName = isEnhanced ? (luckyData?.info.name || fileName) : (data?.file || fileName);

  // Sheet items for tabs
  const sheetItems = useMemo(() => {
    if (isEnhanced && luckyData) {
      return luckyData.sheets.map(s => ({
        key: s.name,
        label: <Space size={2}><TableOutlined style={{ fontSize: 11 }} />{s.name}</Space>,
      }));
    }
    if (!isEnhanced && data) {
      return data.sheets.map(s => ({
        key: s,
        label: <Space size={2}><TableOutlined style={{ fontSize: 11 }} />{s}</Space>,
      }));
    }
    return [];
  }, [isEnhanced, luckyData, data]);

  // ── handlers ───────────────────────────────────────────────────────────
  const handleDownload = useCallback(async () => {
    if (fileId === null || !fileName) return;
    try { await downloadFile(fileId, fileName); } catch (err) { message.error(err instanceof Error ? err.message : '下载失败'); }
  }, [fileId, fileName]);

  const handleRefresh = useCallback(() => {
    if (fileId === null) return;
    if (isEnhanced) loadEnhanced(fileId); else loadFast(fileId);
  }, [fileId, isEnhanced, loadEnhanced, loadFast]);

  // ── render ─────────────────────────────────────────────────────────────
  return (
    <Modal
      title={
        <Space size={12}>
          <FileExcelOutlined style={{ color: '#52c41a', fontSize: 16 }} />
          <Text strong style={{ fontSize: 15 }}>{displayName || 'Excel 预览'}</Text>
          {totalRows > 0 && (
            <Badge count={`${totalRows.toLocaleString()} 行`}
              style={{ backgroundColor: '#1677ff' }} />
          )}
          {sheetCount > 1 && (
            <Badge count={`${sheetCount} 表`}
              style={{ backgroundColor: '#52c41a' }} />
          )}
        </Space>
      }
      open={open} onCancel={onClose}
      width="94vw"
      style={{ top: 12 }}
      styles={{ body: { padding: '12px 16px' } }}
      footer={
        <Space>
          <Button icon={<ReloadOutlined spin={isLoading} />} onClick={handleRefresh}
            disabled={isLoading} size="middle">刷新</Button>
          <Button type="primary" icon={<DownloadOutlined />} onClick={handleDownload} size="middle">
            下载 {fileName || ''}
          </Button>
          <Button onClick={onClose} size="middle">关闭</Button>
        </Space>
      }
      destroyOnHidden
    >
      {/* ── Mode selector + info bar ─────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 12, padding: '8px 12px',
        background: 'linear-gradient(135deg, #fafcff, #f0f5ff)',
        borderRadius: 8, border: '1px solid #e8ecf1',
      }}>
        <Space size={8}>
          <Segmented
            size="small"
            value={mode}
            onChange={(v) => setMode(v as PreviewMode)}
            options={[
              { label: '⚡ 快速预览', value: 'fast' },
              { label: '🔬 增强预览', value: 'enhanced' },
            ]}
          />
          <Tooltip title={mode === 'fast'
            ? '后端从权威存储读取并解析，适合快速浏览表格数据'
            : 'LuckyExcel 客户端解析，显示合并单元格和原始格式，首次加载需下载 ~344KB 脚本'}>
            <Text type="secondary" style={{ fontSize: 11, maxWidth: 280, display: 'inline-block', lineHeight: 1.3 }}>
              {mode === 'fast' ? '后端解析 · 秒开' : '客户端解析 · 含格式'}
            </Text>
          </Tooltip>
        </Space>
        {!isLoading && hasData && (
          <Text type="secondary" style={{ fontSize: 11 }}>
            {currentSheetName}
            {isEnhanced && luckyData && (
              <> · {luckyData.sheets.find(s => s.name === luckySheet)?.config?.mergedCells?.length || 0} 合并单元格</>
            )}
          </Text>
        )}
      </div>

      {/* ── Loading skeleton ─────────────────────────────────────────────── */}
      {isLoading && (
        <div style={{ padding: '8px 0' }}>
          <Skeleton active paragraph={{ rows: 6 }} title={{ width: '40%' }} />
        </div>
      )}

      {/* ── LuckyExcel error fallback ────────────────────────────────────── */}
      {isEnhanced && luckyError && !luckyLoading && (
        <Empty description={<Text type="danger">{luckyError}</Text>} image={Empty.PRESENTED_IMAGE_SIMPLE}>
          <Button type="primary" onClick={() => setMode('fast')}>切换到快速预览</Button>
        </Empty>
      )}

      {/* ── Fast: backend JSON table ─────────────────────────────────────── */}
      {!isEnhanced && data && !fastLoading && (
        <div>
          {sheetCount > 1 && (
            <Tabs activeKey={activeSheet} onChange={handleSheetChange} size="small" type="card"
              items={sheetItems} style={{ marginBottom: 6 }} />
          )}
          <Table size="small"
            dataSource={data.rows.map((r, i) => ({ ...r, _rowIdx: i }))}
            rowKey="_rowIdx" columns={fastCols} rowClassName={rowClassName}
            scroll={{ x: 'max-content', y: 'calc(94vh - 260px)' }}
            pagination={false}
            bordered sticky={{ offsetHeader: 0 }}
          />
        </div>
      )}

      {/* ── Enhanced: LuckyExcel table ───────────────────────────────────── */}
      {isEnhanced && luckyData && !luckyLoading && (
        <div>
          {sheetCount > 1 && (
            <Tabs activeKey={luckySheet} onChange={setLuckySheet} size="small" type="card"
              items={sheetItems} style={{ marginBottom: 6 }} />
          )}
          <Table size="small"
            dataSource={luckyTable.rows} rowKey="_rowIdx"
            columns={luckyCols} rowClassName={rowClassName}
            scroll={{ x: 'max-content', y: 'calc(94vh - 260px)' }}
            pagination={false}
            bordered sticky={{ offsetHeader: 0 }}
          />
        </div>
      )}

      {/* ── Empty ────────────────────────────────────────────────────────── */}
      {!data && !luckyData && !fastLoading && !luckyLoading && !luckyError && (
        <Empty description="无法加载预览数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </Modal>
  );
};

export default ExcelPreview;
