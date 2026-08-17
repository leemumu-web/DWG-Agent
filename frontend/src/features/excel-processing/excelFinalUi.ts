// Excel 第一阶段页面共享的常量与纯展示 helper。
// 独立成文件以控制 ExcelFinalPage.tsx 单文件行数不超过架构限制。

export const TASK_TYPE = 'process_excel_final';
export const ACTIVE_STATUSES = new Set(['queued', 'running']);
export const STATUS_COLOR: Record<string, string> = {
  queued: 'warning',
  running: 'processing',
  succeeded: 'success',
  failed: 'error',
  cancelled: 'default',
};
export const STATUS_TEXT: Record<string, string> = {
  queued: '等待中',
  running: '处理中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

export function numberText(
  value: number | null | undefined,
  digits = 2,
): string {
  return value == null
    ? '-'
    : value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}
