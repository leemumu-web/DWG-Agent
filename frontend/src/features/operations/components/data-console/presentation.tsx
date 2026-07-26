import { Tag } from 'antd';
import type { StorageArea } from '../../types/dataAdmin';

export const STORAGE_PURPOSE_LABELS: Record<string, string> = {
  source_dwg: '原始 DWG',
  derived_dwg: '转换后 DWG',
  reports: '生产报告',
  temporary: '临时文件',
  source_dxf: '原始 DXF',
  derived_dxf: '处理后 DXF',
};

export const STATUS_LABELS: Record<string, string> = {
  succeeded: '成功',
  available: '可用',
  active: '启用',
  ok: '正常',
  degraded: '部分异常',
  queued: '排队中',
  running: '运行中',
  prepared: '已准备',
  failed: '失败',
  error: '异常',
  compensation_required: '待人工补偿',
  cancelled: '已取消',
  deleted: '已删除',
};

export function storageAreaLabel(area?: StorageArea) {
  if (!area) return '未指定存储区';
  const purposes = area.purpose_codes.map(
    (purpose) => STORAGE_PURPOSE_LABELS[purpose] ?? purpose,
  );
  return purposes.length ? purposes.join(' / ') : '其他存储区';
}

export function bucketLabel(value: string | null | undefined, areas: StorageArea[]) {
  const area = areas.find((item) => item.bucket === value);
  return area ? storageAreaLabel(area) : '未配置存储区';
}

export function bytes(value?: number | null) {
  if (!value) return '0 字节';
  const units = ['字节', '千字节', '兆字节', '吉字节', '太字节'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

export function stateTag(status?: string | null) {
  const colors: Record<string, string> = {
    succeeded: 'success',
    available: 'success',
    active: 'success',
    ok: 'success',
    queued: 'processing',
    running: 'processing',
    prepared: 'blue',
    failed: 'error',
    error: 'error',
    compensation_required: 'volcano',
    cancelled: 'default',
    deleted: 'default',
  };
  return <Tag color={colors[status ?? ''] ?? 'default'}>{status ? (STATUS_LABELS[status] ?? '未知状态') : '—'}</Tag>;
}
