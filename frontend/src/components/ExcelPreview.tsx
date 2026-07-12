import { useState, useCallback, useMemo, useEffect, useRef, type FC } from 'react';
import {
  Modal, Table, Skeleton, Empty, Space, Typography, message,
  Tabs, Button, Tooltip, Badge, Segmented,
} from 'antd';
import type { TableColumnsType } from 'antd';
import {
  FileExcelOutlined, DownloadOutlined, ReloadOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { fetchExcelPreview, downloadFile, getFileDownloadUrl } from '../api/files.api';
import { apiClient } from '../api/client';
import type { ExcelPreviewResponse } from '../types/file';

const { Text } = Typography;

// ── LuckyExcel types ─────────────────────────────────────────────────────────

interface LuckyCell {
  v?: string | number | null; m?: string;
  ct?: { fa?: string; t?: string };
  bl?: number; it?: number; bg?: string; fc?: string; fs?: number; ht?: number;
}
interface LuckySheetConfig {
  mergedCells?: Array<{ r: number; c: number; rs: number; cs: number }>;
  columnWidth?: Record<string, number>;
}
interface LuckySheet {
  name: string; index: number; rows?: number; columns?: number;
  data?: LuckyCell[][]; config?: LuckySheetConfig | null;
}
interface LuckyExportJson { sheets: LuckySheet[]; info: { name: string }; }

declare global {
  interface Window {
    LuckyExcel?: {
      transformExcelToLuckyByUrl: (
        url: string, name: string,
        onSuccess: (json: LuckyExportJson, file: unknown) => void,
        onError: (err: Error) => void,
      ) => void;
    };
  }
}

// ── constants ────────────────────────────────────────────────────────────────

const NUMERIC_COLS = new Set([
  '长度(mm)', '数量', '单重(kg)', '总重(kg)', '单毛重(kg)', '总毛重(kg)',
  '总面积(m2)', '面积(m2)', '单面积(m2)', '宽度', '长度',
  '构件数量', '构件总重', '序号', '件数', '重量', '单重', '总重',
  'unit_weight_kg', 'total_weight_kg', 'area_m2', 'length_mm', 'quantity',
  'confidence', 'row_index',
]);

const SUMMARY_MARKERS = ['合计', '合计：', '合 计', '汇总', '总计'];

type PreviewMode = 'fast' | 'enhanced';

// ── helpers ──────────────────────────────────────────────────────────────────

function isSummaryRow(row: Record<string, unknown>): boolean {
  const firstVal = String(Object.values(row)[0] ?? '');
  return SUMMARY_MARKERS.some((m) => firstVal.includes(m));
}

/** Compute optimal column width by sampling content + header length. */
function computeColWidth(
  header: string,
  rows: Record<string, unknown>[],
  dataIndex: string,
): number {
  let maxLen = header.length;
  const sample = Math.min(rows.length, 150);
  for (let i = 0; i < sample; i++) {
    const v = rows[i]?.[dataIndex];
    const s = v === null || v === undefined ? '' : String(v);
    // CJK characters count as ~2 ASCII chars
    const cjk = (s.match(/[一-鿿㐀-䶿]/g) || []).length;
    const len = s.length + cjk * 0.8;
    if (len > maxLen) maxLen = len;
  }
  // ~8px per char + 32px padding, clamped to [70, 400]
  return Math.min(Math.max(Math.ceil(maxLen * 9 + 32), 70), 400);
}

function cellRender(v: unknown): React.ReactNode {
  if (v === null || v === undefined || v === '') return <Text type="secondary">—</Text>;
  if (typeof v === 'number') {
    const formatted = Number.isInteger(v) ? v.toLocaleString() : v.toFixed(3);
    return <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatted}</span>;
  }
  const s = String(v);
  // Auto-detect numeric strings (common in raw_like_original sheet where
  // openpyxl returns numbers as strings from data_only mode)
  const numericStr = s.match(/^-?\d+(\.\d+)?$/);
  if (numericStr) {
    const n = parseFloat(s);
    return <span style={{ fontVariantNumeric: 'tabular-nums' }}>
      {Number.isInteger(n) ? n.toLocaleString() : n.toFixed(3)}
    </span>;
  }
  if (SUMMARY_MARKERS.includes(s)) return <Text strong style={{ color: '#1677ff' }}>{s}</Text>;
  return <span title={s.length > 30 ? s : undefined}>{s}</span>;
}

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
  const luckyTable = useMemo(() => {
    if (!luckyData) return { headers: [] as string[], rows: [] as Record<string, unknown>[], mergeMap: new Map<string, { rowSpan: number; colSpan: number }>(), colWidths: {} as Record<number, number> };
    const sheet = luckyData.sheets.find(s => s.name === luckySheet) || luckyData.sheets[0];
    if (!sheet?.data) return { headers: [] as string[], rows: [] as Record<string, unknown>[], mergeMap: new Map(), colWidths: {} };

    const rawData = sheet.data;
    let maxCols = sheet.columns || 0;
    for (const row of rawData) { if (Array.isArray(row) && row.length > maxCols) maxCols = row.length; }

    const headerRow = Array.isArray(rawData[0]) ? rawData[0].slice(0, maxCols) : [];
    const headers = headerRow.map((c, idx) => {
      if (!c) return `Col${idx + 1}`;
      return String((typeof c === 'object' ? (c.m || c.v || `Col${idx + 1}`) : c) ?? `Col${idx + 1}`);
    });

    const mm = new Map<string, { rowSpan: number; colSpan: number }>();
    sheet.config?.mergedCells?.forEach(mc => {
      const dr = mc.r - 1;
      if (dr < 0) return;
      mm.set(`${dr}-${mc.c}`, { rowSpan: mc.rs, colSpan: mc.cs });
      for (let r = 0; r < mc.rs; r++)
        for (let c = 0; c < mc.cs; c++)
          if (r || c) { const k = `${dr + r}-${mc.c + c}`; if (!mm.has(k)) mm.set(k, { rowSpan: 0, colSpan: 0 }); }
    });

    const cw: Record<number, number> = {};
    if (sheet.config?.columnWidth) {
      for (const [k, v] of Object.entries(sheet.config.columnWidth)) {
        const idx = parseInt(k, 10);
        if (!isNaN(idx) && v > 0) cw[idx] = Math.min(Math.max(v * 0.75, 60), 400);
      }
    }

    const rows: Record<string, unknown>[] = [];
    for (let r = 1; r < rawData.length; r++) {
      const row: Record<string, unknown> = { _rowIdx: r };
      const rawRow = Array.isArray(rawData[r]) ? rawData[r] : [];
      for (let c = 0; c < maxCols; c++) {
        const key = headers[c] || `Col${c + 1}`;
        const cell = c < rawRow.length ? rawRow[c] : null;
        if (cell === null || cell === undefined) { row[key] = null; continue; }
        if (typeof cell === 'object') {
          const v = cell.m || cell.v;
          const n = Number(v);
          row[key] = (!isNaN(n) && v !== '' && v !== null) ? n : (v ?? null);
          if (cell.bl === 1 || cell.bg || cell.fc)
            row[`_fmt_${key}`] = { bold: cell.bl === 1, italic: cell.it === 1, bg: cell.bg, fc: cell.fc, ht: cell.ht };
        } else {
          const n = Number(cell);
          row[key] = (!isNaN(n) && typeof cell !== 'boolean') ? n : cell;
        }
      }
      rows.push(row);
    }
    return { headers, rows, mergeMap: mm, colWidths: cw };
  }, [luckyData, luckySheet]);

  // ── column builders ────────────────────────────────────────────────────

  /** Fast columns: content-aware widths from sampling actual data. */
  const fastCols = useMemo((): TableColumnsType<Record<string, unknown>> => {
    if (!data?.headers) return [];
    const rows = data.rows as Record<string, unknown>[];
    return data.headers.map((h) => {
      const numeric = NUMERIC_COLS.has(String(h));
      const w = computeColWidth(String(h), rows, String(h));
      return {
        title: h, dataIndex: h, key: h, width: w,
        align: (numeric ? 'right' : 'left') as 'right' | 'left',
        onHeaderCell: () => ({ style: { whiteSpace: 'nowrap', fontWeight: 600, fontSize: 12 } }),
        render: cellRender,
      };
    });
  }, [data]);

  /** Enhanced columns: LuckyExcel widths + merged cell spans. */
  const luckyCols = useMemo((): TableColumnsType<Record<string, unknown>> => {
    const { headers, colWidths, mergeMap } = luckyTable;
    return headers.map((h, idx) => {
      const numeric = NUMERIC_COLS.has(String(h));
      const w = (colWidths as Record<number, number>)[idx];
      return {
        title: h, dataIndex: h, key: h,
        width: w || undefined,
        align: (numeric ? 'right' : 'left') as 'right' | 'left',
        onHeaderCell: () => ({ style: { whiteSpace: 'nowrap', fontWeight: 600, fontSize: 12 } }),
        onCell: (record: Record<string, unknown>) => {
          const rIdx = record._rowIdx as number;
          const merge = mergeMap.get(`${rIdx}-${idx}`);
          const res: Record<string, unknown> = {};
          if (merge) {
            if (merge.rowSpan === 0) res.rowSpan = 0; else if (merge.rowSpan > 1) res.rowSpan = merge.rowSpan;
            if (merge.colSpan === 0) res.colSpan = 0; else if (merge.colSpan > 1) res.colSpan = merge.colSpan;
          }
          const fmt = record[`_fmt_${h}`] as Record<string, unknown> | undefined;
          if (fmt) {
            const style: Record<string, string> = {};
            if (fmt.bold) style.fontWeight = 'bold';
            if (fmt.bg) style.backgroundColor = fmt.bg as string;
            if (fmt.fc) style.color = fmt.fc as string;
            if (fmt.ht !== undefined) style.textAlign = (['left','center','right'] as const)[fmt.ht as number] || 'left';
            if (Object.keys(style).length) res.style = style;
          }
          return res;
        },
        render: cellRender,
      };
    });
  }, [luckyTable]);

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
