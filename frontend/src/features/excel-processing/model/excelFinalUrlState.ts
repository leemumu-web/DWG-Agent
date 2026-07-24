export const DEFAULT_BATCH_PAGE_SIZE = 20;
export const DEFAULT_SEARCH_PAGE_SIZE = 20;
export type ExcelFinalTab = 'process' | 'batches' | 'parts' | 'handbook';
const EXCEL_FINAL_TABS = new Set<ExcelFinalTab>(['process', 'batches', 'parts', 'handbook']);

const BATCH_PAGE_SIZES = new Set([10, 20, 50, 100, 200]);
const SEARCH_PAGE_SIZES = new Set([10, 20, 50, 100, 200]);

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function allowedPageSize(
  value: string | null,
  allowed: Set<number>,
  fallback: number,
): number {
  const parsed = positiveInt(value, fallback);
  return allowed.has(parsed) ? parsed : fallback;
}

function textValue(value: string | null): string {
  return value?.trim() ?? '';
}

export interface ExcelFinalUrlState {
  tab: ExcelFinalTab;
  jobId: number | null;
  batchPage: number;
  batchPageSize: number;
  batchId: number | null;
  partNo: string;
  spec: string;
  material: string;
  searchApplied: boolean;
  searchPage: number;
  searchPageSize: number;
}

export function parseExcelFinalUrlState(params: URLSearchParams): ExcelFinalUrlState {
  const jobId = positiveInt(params.get('job_id'), 0) || null;
  const batchId = positiveInt(params.get('batch_id'), 0) || null;
  const partNo = textValue(params.get('part_no'));
  const spec = textValue(params.get('spec'));
  const material = textValue(params.get('material'));
  const requestedTab = params.get('tab') as ExcelFinalTab | null;
  return {
    tab: requestedTab && EXCEL_FINAL_TABS.has(requestedTab) ? requestedTab : 'process',
    jobId,
    batchPage: positiveInt(params.get('batch_page'), 1),
    batchPageSize: allowedPageSize(
      params.get('batch_size'),
      BATCH_PAGE_SIZES,
      DEFAULT_BATCH_PAGE_SIZE,
    ),
    batchId,
    partNo,
    spec,
    material,
    searchApplied: params.get('search') === '1' || Boolean(partNo || spec || material),
    searchPage: positiveInt(params.get('search_page'), 1),
    searchPageSize: allowedPageSize(
      params.get('search_size'),
      SEARCH_PAGE_SIZES,
      DEFAULT_SEARCH_PAGE_SIZE,
    ),
  };
}

export function mergeExcelFinalParams(
  current: URLSearchParams,
  changes: Record<string, string | number | null | undefined>,
): URLSearchParams {
  const next = new URLSearchParams(current);
  for (const [key, value] of Object.entries(changes)) {
    if (value == null || value === '') next.delete(key);
    else next.set(key, String(value));
  }
  return next;
}

export function omitDefault(value: number, defaultValue: number): number | null {
  return value === defaultValue ? null : value;
}
