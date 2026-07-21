import type { ReactNode } from 'react';
import { Typography } from 'antd';
import type { TableColumnsType } from 'antd';

const { Text } = Typography;

export interface LuckyCell {
  v?: string | number | null;
  m?: string;
  ct?: { fa?: string; t?: string };
  bl?: number;
  it?: number;
  bg?: string;
  fc?: string;
  fs?: number;
  ht?: number;
}

export interface LuckySheetConfig {
  mergedCells?: Array<{ r: number; c: number; rs: number; cs: number }>;
  columnWidth?: Record<string, number>;
}

export interface LuckySheet {
  name: string;
  index: number;
  rows?: number;
  columns?: number;
  data?: LuckyCell[][];
  config?: LuckySheetConfig | null;
}

export interface LuckyExportJson {
  sheets: LuckySheet[];
  info: { name: string };
}

declare global {
  interface Window {
    LuckyExcel?: {
      transformExcelToLuckyByUrl: (
        url: string,
        name: string,
        onSuccess: (json: LuckyExportJson, file: unknown) => void,
        onError: (error: Error) => void,
      ) => void;
    };
  }
}

const NUMERIC_COLS = new Set([
  '长度(mm)', '数量', '单重(kg)', '总重(kg)', '单毛重(kg)', '总毛重(kg)',
  '总面积(m2)', '面积(m2)', '单面积(m2)', '宽度', '长度',
  '构件数量', '构件总重', '序号', '件数', '重量', '单重', '总重',
  'unit_weight_kg', 'total_weight_kg', 'area_m2', 'length_mm', 'quantity',
  'confidence', 'row_index',
]);

const SUMMARY_MARKERS = ['合计', '合计：', '合 计', '汇总', '总计'];

export type PreviewMode = 'fast' | 'enhanced';

export function isSummaryRow(row: Record<string, unknown>): boolean {
  const firstValue = String(Object.values(row)[0] ?? '');
  return SUMMARY_MARKERS.some((marker) => firstValue.includes(marker));
}

function computeColWidth(header: string, rows: Record<string, unknown>[], dataIndex: string): number {
  let maxLength = header.length;
  const sample = Math.min(rows.length, 150);
  for (let index = 0; index < sample; index += 1) {
    const value = rows[index]?.[dataIndex];
    const text = value === null || value === undefined ? '' : String(value);
    const cjkCount = (text.match(/[一-鿿㐀-䶿]/g) || []).length;
    maxLength = Math.max(maxLength, text.length + cjkCount * 0.8);
  }
  return Math.min(Math.max(Math.ceil(maxLength * 9 + 32), 70), 400);
}

function cellRender(value: unknown): ReactNode {
  if (value === null || value === undefined || value === '') return <Text type="secondary">—</Text>;
  if (typeof value === 'number') {
    const formatted = Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
    return <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatted}</span>;
  }
  const text = String(value);
  if (/^-?\d+(\.\d+)?$/.test(text)) {
    const numericValue = Number.parseFloat(text);
    return <span style={{ fontVariantNumeric: 'tabular-nums' }}>
      {Number.isInteger(numericValue) ? numericValue.toLocaleString() : numericValue.toFixed(3)}
    </span>;
  }
  if (SUMMARY_MARKERS.includes(text)) return <Text strong style={{ color: '#1677ff' }}>{text}</Text>;
  return <span title={text.length > 30 ? text : undefined}>{text}</span>;
}

export interface LuckyTableModel {
  headers: string[];
  rows: Record<string, unknown>[];
  mergeMap: Map<string, { rowSpan: number; colSpan: number }>;
  colWidths: Record<number, number>;
}

export function buildLuckyTable(data: LuckyExportJson | null, sheetName: string): LuckyTableModel {
  if (!data) return { headers: [], rows: [], mergeMap: new Map(), colWidths: {} };
  const sheet = data.sheets.find((candidate) => candidate.name === sheetName) || data.sheets[0];
  if (!sheet?.data) return { headers: [], rows: [], mergeMap: new Map(), colWidths: {} };

  const rawData = sheet.data;
  let maxColumns = sheet.columns || 0;
  for (const row of rawData) {
    if (Array.isArray(row) && row.length > maxColumns) maxColumns = row.length;
  }
  const headerRow = Array.isArray(rawData[0]) ? rawData[0].slice(0, maxColumns) : [];
  const headers = headerRow.map((cell, index) => {
    if (!cell) return `Col${index + 1}`;
    return String((typeof cell === 'object' ? (cell.m || cell.v || `Col${index + 1}`) : cell) ?? `Col${index + 1}`);
  });

  const mergeMap = new Map<string, { rowSpan: number; colSpan: number }>();
  sheet.config?.mergedCells?.forEach((mergedCell) => {
    const dataRow = mergedCell.r - 1;
    if (dataRow < 0) return;
    mergeMap.set(`${dataRow}-${mergedCell.c}`, { rowSpan: mergedCell.rs, colSpan: mergedCell.cs });
    for (let row = 0; row < mergedCell.rs; row += 1) {
      for (let column = 0; column < mergedCell.cs; column += 1) {
        if (row || column) {
          const key = `${dataRow + row}-${mergedCell.c + column}`;
          if (!mergeMap.has(key)) mergeMap.set(key, { rowSpan: 0, colSpan: 0 });
        }
      }
    }
  });

  const colWidths: Record<number, number> = {};
  for (const [key, value] of Object.entries(sheet.config?.columnWidth ?? {})) {
    const index = Number.parseInt(key, 10);
    if (!Number.isNaN(index) && value > 0) colWidths[index] = Math.min(Math.max(value * 0.75, 60), 400);
  }

  const rows: Record<string, unknown>[] = [];
  for (let rowIndex = 1; rowIndex < rawData.length; rowIndex += 1) {
    const row: Record<string, unknown> = { _rowIdx: rowIndex };
    const rawRow = Array.isArray(rawData[rowIndex]) ? rawData[rowIndex] : [];
    for (let column = 0; column < maxColumns; column += 1) {
      const key = headers[column] || `Col${column + 1}`;
      const cell = column < rawRow.length ? rawRow[column] : null;
      if (cell === null || cell === undefined) {
        row[key] = null;
      } else if (typeof cell === 'object') {
        const value = cell.m || cell.v;
        const numericValue = Number(value);
        row[key] = (!Number.isNaN(numericValue) && value !== '' && value !== null) ? numericValue : (value ?? null);
        if (cell.bl === 1 || cell.bg || cell.fc) {
          row[`_fmt_${key}`] = { bold: cell.bl === 1, italic: cell.it === 1, bg: cell.bg, fc: cell.fc, ht: cell.ht };
        }
      } else {
        const numericValue = Number(cell);
        row[key] = (!Number.isNaN(numericValue) && typeof cell !== 'boolean') ? numericValue : cell;
      }
    }
    rows.push(row);
  }
  return { headers, rows, mergeMap, colWidths };
}

export function buildFastColumns(headers: Array<string | number>, rows: Record<string, unknown>[]): TableColumnsType<Record<string, unknown>> {
  return headers.map((header) => {
    const key = String(header);
    const numeric = NUMERIC_COLS.has(key);
    return {
      title: header,
      dataIndex: header,
      key,
      width: computeColWidth(key, rows, key),
      align: numeric ? 'right' : 'left',
      onHeaderCell: () => ({ style: { whiteSpace: 'nowrap', fontWeight: 600, fontSize: 12 } }),
      render: cellRender,
    };
  });
}

export function buildLuckyColumns(model: LuckyTableModel): TableColumnsType<Record<string, unknown>> {
  return model.headers.map((header, index) => ({
    title: header,
    dataIndex: header,
    key: header,
    width: model.colWidths[index] || undefined,
    align: NUMERIC_COLS.has(header) ? 'right' : 'left',
    onHeaderCell: () => ({ style: { whiteSpace: 'nowrap', fontWeight: 600, fontSize: 12 } }),
    onCell: (record) => {
      const rowIndex = record._rowIdx as number;
      const merge = model.mergeMap.get(`${rowIndex}-${index}`);
      const result: Record<string, unknown> = {};
      if (merge) {
        if (merge.rowSpan === 0) result.rowSpan = 0;
        else if (merge.rowSpan > 1) result.rowSpan = merge.rowSpan;
        if (merge.colSpan === 0) result.colSpan = 0;
        else if (merge.colSpan > 1) result.colSpan = merge.colSpan;
      }
      const format = record[`_fmt_${header}`] as Record<string, unknown> | undefined;
      if (format) {
        const style: Record<string, string> = {};
        if (format.bold) style.fontWeight = 'bold';
        if (format.bg) style.backgroundColor = format.bg as string;
        if (format.fc) style.color = format.fc as string;
        if (format.ht !== undefined) style.textAlign = (['left', 'center', 'right'] as const)[format.ht as number] || 'left';
        if (Object.keys(style).length) result.style = style;
      }
      return result;
    },
    render: cellRender,
  }));
}
