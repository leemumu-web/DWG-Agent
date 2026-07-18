import axios from 'axios';
import { apiClient, fetchAllPages, type ApiEnvelope, type PageEnvelope } from './client';
import type {
  BatchInfo,
  DxfPreviewResponse,
  ExcelPreviewResponse,
  StoredFile,
} from '../types/file';
import type { Job } from '../types/job';
import { describeApiError, describeApiErrorAsync } from './error';

/** Fetch ALL files, optionally filtered by batch_name and/or file_ext. */
export async function listFiles(batchName?: string, fileExt?: string) {
  const params: Record<string, unknown> = {};
  if (batchName) params.batch_name = batchName;
  if (fileExt) params.file_ext = fileExt;
  return fetchAllPages<StoredFile>('/api/v1/files', params);
}

export interface FileListParams {
  page: number;
  page_size: number;
  batch_name?: string;
  file_ext?: string;
  sort_by?: string;
  sort_dir?: 'asc' | 'desc';
}

export async function listFilesPage(params: FileListParams) {
  const res = await apiClient.get<PageEnvelope<StoredFile>>('/api/v1/files', { params });
  return res.data;
}

/** List distinct batches (folder names), optionally filtered by file_ext. */
export async function listBatches(fileExt?: string) {
  const params: Record<string, unknown> = {};
  if (fileExt) params.file_ext = fileExt;
  const res = await apiClient.get<ApiEnvelope<BatchInfo[]>>('/api/v1/files/batches', { params });
  return res.data.data;
}

export async function uploadFile(file: File, batchName?: string): Promise<StoredFile> {
  const form = new FormData();
  form.append('upload', file);
  const res = await apiClient.post<ApiEnvelope<StoredFile>>('/api/v1/files', form, {
    params: batchName ? { batch_name: batchName } : undefined,
    timeout: 120_000,
  });
  return res.data.data;
}

export async function uploadFileAndConvert(file: File, batchName?: string) {
  const stored = await uploadFile(file, batchName);
  const res = await apiClient.post<ApiEnvelope<Job>>('/api/v1/jobs', {
    task_type: 'convert_dwg_to_dxf',
    precision_level: 'normal',
    params: { file_id: stored.id, batch_name: batchName || null },
  }, {
    timeout: 30_000,
  });
  return { file: stored, job: res.data.data };
}

export interface ZipUploadResult {
  batch_name: string;
  files: StoredFile[];
  success_count: number;
  skipped_count: number;
}

/** Upload a .zip archive — backend extracts matching files and returns them as a batch. */
export async function uploadZip(file: File, fileExt: string): Promise<ZipUploadResult> {
  const form = new FormData();
  form.append('upload', file);
  const res = await apiClient.post<ApiEnvelope<ZipUploadResult>>('/api/v1/files/upload-zip', form, {
    params: { file_ext: fileExt },
    timeout: 300_000,
  });
  return res.data.data;
}

export async function getFileDownloadUrl(fileId: number) {
  const res = await apiClient.get<ApiEnvelope<{ url: string; expires_in: number }>>(
    `/api/v1/files/${fileId}/download-url`
  );
  return res.data.data;
}

function isRetryableDownloadError(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false;
  const status = error.response?.status;
  return status === undefined || status === 403 || status === 408 || status === 429 || status >= 500;
}

async function downloadError(error: unknown): Promise<Error> {
  return new Error(await describeApiErrorAsync(error, '下载失败'));
}

/** Download through a short-lived signed URL; every retry obtains a new signature. */
export async function downloadFile(fileId: number, filename: string): Promise<void> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const { url } = await getFileDownloadUrl(fileId);
      const res = await apiClient.get<Blob>(url, {
        responseType: 'blob',
        timeout: 120_000,
      });
      triggerBlobDownload(res.data, filename);
      return;
    } catch (error) {
      lastError = error;
      if (attempt === 1 || !isRetryableDownloadError(error)) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  throw await downloadError(lastError);
}

function triggerBlobDownload(blob: Blob, filename: string) {
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  }, 100);
}

/** POST zip-download payload, receive streaming zip, trigger browser save. */
export async function downloadZip(
  fileIds: number[],
  formats: string[],
  folderName: string,
): Promise<void> {
  try {
    const res = await apiClient.post<Blob>('/api/v1/files/download-zip', {
      file_ids: fileIds,
      formats,
      folder_name: folderName,
    }, {
      responseType: 'blob',
      timeout: 300_000,
    });
    triggerBlobDownload(res.data, `${folderName}.zip`);
  } catch (error) {
    throw await downloadError(error);
  }
}

export interface ZipFormatAvailability {
  format: 'dwg' | 'dxf';
  available_count: number;
  missing_count: number;
  missing_file_ids: number[];
  complete: boolean;
}

export interface ZipAvailabilityPreview {
  file_count: number;
  formats: ZipFormatAvailability[];
  can_download: boolean;
}

/** Check that every selected file has every requested ZIP format. */
export async function previewZip(
  fileIds: number[],
  formats: Array<'dwg' | 'dxf'>,
  folderName: string,
): Promise<ZipAvailabilityPreview> {
  const res = await apiClient.post<ApiEnvelope<ZipAvailabilityPreview>>(
    '/api/v1/files/download-zip/preview',
    { file_ids: fileIds, formats, folder_name: folderName },
  );
  return res.data.data;
}

/** Soft-delete multiple files at once. */
export async function bulkDeleteFiles(fileIds: number[]): Promise<void> {
  await apiClient.post('/api/v1/files/bulk-delete', { file_ids: fileIds });
}

export interface BatchBulkDeleteResult {
  deleted_batch_count: number;
  deleted_file_count: number;
  cancelled_job_count: number;
}

/** Atomically soft-delete complete batches, including generated results. */
export async function bulkDeleteBatches(batchNames: string[]): Promise<BatchBulkDeleteResult> {
  const res = await apiClient.post<ApiEnvelope<BatchBulkDeleteResult>>(
    '/api/v1/files/batches/bulk-delete',
    { batch_names: batchNames },
  );
  return res.data.data;
}

/** Upload a folder — process matching files with a bounded concurrent pool.
 *  The folder name becomes the batch_name for all files in it.
 *  @param fileExt  only upload files matching this extension (e.g. '.dwg', '.dxf')
 *  @param onFile   upload+convert callback: receives (file, batchName) → result */
export async function uploadFolder(
  files: File[],
  batchName: string,
  opts?: {
    fileExt?: string;
    concurrency?: number;
    onFile?: (file: File, batchName: string) => Promise<unknown>;
    onProgress?: (processed: number, total: number, success: number) => void;
  },
): Promise<{
  total: number;
  success: number;
  results: unknown[];
  failures: Array<{ file_name: string; reason: string }>;
}> {
  const ext = opts?.fileExt || '.dwg';
  const onFile = opts?.onFile || ((f: File, bn: string) => uploadFileAndConvert(f, bn));
  const matched = files.filter((f) => f.name.toLowerCase().endsWith(ext));
  if (matched.length === 0) return { total: 0, success: 0, results: [], failures: [] };

  const queue = [...matched];
  let success = 0;
  let processed = 0;
  const results: unknown[] = [];
  const failures: Array<{ file_name: string; reason: string }> = [];

  const worker = async () => {
    while (queue.length > 0) {
      const f = queue.shift()!;
      try {
        results.push(await onFile(f, batchName));
        success++;
      } catch (error) {
        failures.push({ file_name: f.name, reason: describeApiError(error, '上传失败') });
      }
      processed++;
      opts?.onProgress?.(processed, matched.length, success);
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(opts?.concurrency ?? 4, matched.length) }, () => worker()),
  );

  return { total: matched.length, success, results, failures };
}

/** Soft-delete all files in a batch (folder). */
export async function deleteBatch(batchName: string): Promise<void> {
  await apiClient.delete(`/api/v1/files/batches/${encodeURIComponent(batchName)}`);
}

/** Download all files in a batch as a ZIP archive. */
export async function downloadBatchZip(batchName: string): Promise<void> {
  const res = await apiClient.get<Blob>(
    `/api/v1/files/batches/${encodeURIComponent(batchName)}/download-zip`,
    { responseType: 'blob', timeout: 300_000 },
  );
  triggerBlobDownload(res.data, `${batchName}.zip`);
}

/** Fetch Excel file preview data (sheets, headers, rows) from backend. */
export async function fetchExcelPreview(
  fileId: number,
  sheet?: string,
): Promise<ExcelPreviewResponse> {
  const params: Record<string, unknown> = {};
  if (sheet) params.sheet = sheet;
  const res = await apiClient.get<ApiEnvelope<ExcelPreviewResponse>>(
    `/api/v1/files/${fileId}/excel-preview`,
    { params },
  );
  return res.data.data;
}

/** Generate or reuse a registered DXF SVG preview and return its metadata. */
export async function fetchDxfPreview(fileId: number): Promise<DxfPreviewResponse> {
  const res = await apiClient.get<ApiEnvelope<DxfPreviewResponse>>(
    `/api/v1/files/${fileId}/dxf-preview`,
    { timeout: 120_000 },
  );
  return res.data.data;
}

/** Fetch preview bytes with the normal Bearer interceptor; `<img>` cannot add it. */
export async function fetchDxfPreviewBlob(
  contentUrl: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const res = await apiClient.get<Blob>(contentUrl, {
    responseType: 'blob',
    signal,
    timeout: 120_000,
  });
  return res.data;
}
