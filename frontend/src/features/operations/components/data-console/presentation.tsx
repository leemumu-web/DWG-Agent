import { Tag } from 'antd';

export const BUCKETS = ['dwg-original', 'dwg-derived', 'dwg-reports', 'dwg-temp', 'dxf-original', 'dxf-derived'];
export const ACTIVE_SCAN = new Set(['queued', 'running']);
export const STATUS_LABELS: Record<string, string> = {
  succeeded: '成功',
  available: '可用',
  ok: '正常',
  open: '待处置',
  resolved: '已处置',
  queued: '排队中',
  running: '运行中',
  in_progress: '进行中',
  prepared: '已准备',
  failed: '失败',
  error: '异常',
  compensation_required: '待补偿',
  cancelled: '已取消',
  deleted: '已删除',
};
export const FINDING_LABELS: Record<string, string> = {
  missing_object: '登记对象缺失',
  untracked_object: '未登记对象',
  size_mismatch: '大小不一致',
  retained_deleted: '软删除对象保留',
};
export const DIRECTION_LABELS: Record<string, string> = {
  inbound: '入库',
  outbound: '出库',
  internal: '内部',
};
export const OPERATION_LABELS: Record<string, string> = {
  upload: '单文件上传',
  upload_zip: 'ZIP 导入',
  generated: '生成文件登记',
  download: '单文件下载',
  download_zip: 'ZIP 出库',
  soft_delete: '软删除',
  restore: '恢复登记',
  register_existing: '补登记对象',
  soft_delete_missing: '软删除缺失登记',
  purge_untracked: '永久清理对象',
};

export function bytes(value?: number | null) {
  if (!value) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

export function stateTag(status?: string | null) {
  const colors: Record<string, string> = {
    succeeded: 'success', available: 'success', ok: 'success',
    queued: 'processing', running: 'processing', in_progress: 'processing', prepared: 'blue',
    failed: 'error', error: 'error', compensation_required: 'volcano',
    cancelled: 'default', deleted: 'default', resolved: 'success', open: 'warning',
  };
  return <Tag color={colors[status ?? ''] ?? 'default'}>{status ? (STATUS_LABELS[status] ?? status) : '—'}</Tag>;
}
