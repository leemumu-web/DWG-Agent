import type { ReactNode } from 'react';
import { Typography } from 'antd';
import type { TableColumnsType } from 'antd';

const { Text } = Typography;

const NUMERIC_COLS = new Set([
  '长度(mm)', '数量', '单重(kg)', '总重(kg)', '单毛重(kg)', '总毛重(kg)',
  '总面积(m2)', '面积(m2)', '单面积(m2)', '宽度', '长度',
  '构件数量', '构件总重', '序号', '件数', '重量', '单重', '总重',
  'unit_weight_kg', 'total_weight_kg', 'area_m2', 'length_mm', 'quantity',
  'confidence', 'row_index',
]);

const SUMMARY_MARKERS = ['合计', '合计：', '合 计', '汇总', '总计'];

export function isSummaryRow(row: Record<string, unknown>): boolean {
  const firstValue = String(Object.values(row)[0] ?? '');
  return SUMMARY_MARKERS.some((marker) => firstValue.includes(marker));
}

function computeColWidth(
  header: string,
  rows: Record<string, unknown>[],
  dataIndex: string,
): number {
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
  if (value === null || value === undefined || value === '') {
    return <Text type="secondary">—</Text>;
  }
  if (typeof value === 'number') {
    const formatted = Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
    return <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatted}</span>;
  }
  const text = String(value);
  if (/^-?\d+(\.\d+)?$/.test(text)) {
    const numericValue = Number.parseFloat(text);
    return (
      <span style={{ fontVariantNumeric: 'tabular-nums' }}>
        {Number.isInteger(numericValue)
          ? numericValue.toLocaleString()
          : numericValue.toFixed(3)}
      </span>
    );
  }
  if (SUMMARY_MARKERS.includes(text)) {
    return <Text strong style={{ color: '#1677ff' }}>{text}</Text>;
  }
  return <span title={text.length > 30 ? text : undefined}>{text}</span>;
}

export function buildFastColumns(
  headers: Array<string | number>,
  rows: Record<string, unknown>[],
): TableColumnsType<Record<string, unknown>> {
  return headers.map((header) => {
    const key = String(header);
    const numeric = NUMERIC_COLS.has(key);
    return {
      title: header,
      dataIndex: header,
      key,
      width: computeColWidth(key, rows, key),
      align: numeric ? 'right' : 'left',
      onHeaderCell: () => ({
        style: {
          whiteSpace: 'nowrap',
          fontWeight: 600,
          fontSize: 12,
        },
      }),
      render: cellRender,
    };
  });
}
