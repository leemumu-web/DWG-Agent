import axios from 'axios';
import {
  apiClient,
  completedTransferProgress,
  downloadBlob,
  fetchAllPages,
  initialTransferProgress,
  transferProgressFromAxios,
  triggerBlobDownload,
  type ApiEnvelope,
  type PageEnvelope,
  type TransferProgressHandler,
} from '../../shared/api';
import type {
  BatchInfo,
  DxfPreviewResponse,
  ExcelPreviewResponse,
  StoredFile,
} from './file';
import type { Job } from '../jobs';
import { describeApiError, describeApiErrorAsync } from '../../shared/api';

/** One operator submission may contain up to 5000 drawings. */
export const MAX_FOLDER_FILES = 5000;

export interface LimitedFolderSelection {
  files: File[];
  omittedCount: number;
}

/** Return the operator-visible maximum number of files for a selected folder. */
export function limitFolderUploadFiles(files: File[]): LimitedFolderSelection {
  return {
    files: files.slice(0, MAX_FOLDER_FILES),
    omittedCount: Math.max(0, files.length - MAX_FOLDER_FILES),
  };
}

/** Fetch ALL files, optionally filtered by batch_name and/or file_ext. */
export async function listFiles(batchName?: string, fileExt?: string, standaloneOnly = false) {
  const params: Record<string, unknown> = {};
  if (batchName) params.batch_name = batchName;
  if (fileExt) params.file_ext = fileExt;
  if (standaloneOnly) params.standalone_only = true;
  return fetchAllPages<StoredFile>('/api/v1/files', params);
}

export interface FileListParams {
  page: number;
  page_size: number;
  batch_name?: string;
  file_ext?: string;
  sort_by?: string;
  sort_dir?: 'asc' | 'desc';
  standalone_only?: boolean;
}

export async function listFilesPage(params: FileListParams) {
  const res = await apiClient.get<PageEnvelope<StoredFile>>('/api/v1/files', { params });
  return res.data;
}

/** Read the authoritative file record before presenting or downloading a result. */
export async function getFile(fileId: number): Promise<StoredFile> {
  const res = await apiClient.get<ApiEnvelope<StoredFile>>(`/api/v1/files/${fileId}`);
  return res.data.data;
}

/** List distinct batches (folder names), optionally filtered by file_ext. */
export async function listBatches(fileExt?: string, standaloneOnly = false) {
  const params: Record<string, unknown> = {};
  if (fileExt) params.file_ext = fileExt;
  if (standaloneOnly) params.standalone_only = true;
  const res = await apiClient.get<ApiEnvelope<BatchInfo[]>>('/api/v1/files/batches', { params });
  return res.data.data;
}

export async function uploadFile(
  file: File,
  batchName?: string,
  idempotencyKey?: string,
  onProgress?: TransferProgressHandler,
): Promise<StoredFile> {
  const form = new FormData();
  form.append('upload', file);
  onProgress?.(initialTransferProgress(file.size));
  const res = await apiClient.post<ApiEnvelope<StoredFile>>('/api/v1/files', form, {
    params: batchName ? { batch_name: batchName } : undefined,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    timeout: 120_000,
    onUploadProgress: (event) => onProgress?.(transferProgressFromAxios(event, file.size)),
  });
  onProgress?.(completedTransferProgress(file.size, file.size));
  return res.data.data;
}

export async function uploadFileAndConvert(
  file: File,
  batchName?: string,
  onProgress?: TransferProgressHandler,
) {
  const stored = await uploadFile(file, batchName, undefined, onProgress);
  const res = await apiClient.post<ApiEnvelope<Job>>('/api/v1/workflows/jobs', {
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
export async function uploadZip(
  file: File,
  fileExt: string,
  onProgress?: TransferProgressHandler,
): Promise<ZipUploadResult> {
  const form = new FormData();
  form.append('upload', file);
  onProgress?.(initialTransferProgress(file.size));
  const res = await apiClient.post<ApiEnvelope<ZipUploadResult>>('/api/v1/files/upload-zip', form, {
    params: { file_ext: fileExt },
    timeout: 300_000,
    onUploadProgress: (event) => onProgress?.(transferProgressFromAxios(event, file.size)),
  });
  onProgress?.(completedTransferProgress(file.size, file.size));
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
  // 重试策略：签名 URL 可能刚生成即过期，故 403/408/429/5xx 重试一次并
  // 重新取签名（间隔 500ms）；401 交由全局拦截器刷新会话，不在此重试。
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

/** POST zip-download payload, receive streaming zip, trigger browser save. */
export async function downloadZip(
  fileIds: number[],
  formats: string[],
  folderName: string,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
): Promise<void> {
  return downloadBlob({
    url: '/api/v1/files/download-zip',
    method: 'POST',
    data: { file_ids: fileIds, formats, folder_name: folderName },
    fallbackName: `${folderName}.zip`,
    errorMessage: '打包下载失败',
    onProgress,
    signal,
  });
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
    onFile?: (
      file: File,
      batchName: string,
      onProgress?: TransferProgressHandler,
    ) => Promise<unknown>;
    onProgress?: (processed: number, total: number, success: number) => void;
    onTransferProgress?: TransferProgressHandler;
  },
): Promise<{
  total: number;
  success: number;
  results: unknown[];
  failures: Array<{ file_name: string; reason: string }>;
}> {
  const ext = opts?.fileExt || '.dwg';
  const onFile = opts?.onFile || ((
    f: File,
    bn: string,
    onProgress?: TransferProgressHandler,
  ) => uploadFileAndConvert(f, bn, onProgress));
  const limited = limitFolderUploadFiles(files);
  const matched = limited.files.filter((f) => f.name.toLowerCase().endsWith(ext));
  if (matched.length === 0) return { total: 0, success: 0, results: [], failures: [] };

  const queue = [...matched];
  let success = 0;
  let processed = 0;
  const results: unknown[] = [];
  const failures: Array<{ file_name: string; reason: string }> = [];
  const totalBytes = matched.reduce((total, file) => total + file.size, 0);
  const loadedByFile = new Map<File, number>();
  const reportTransfer = (file: File, loadedBytes: number) => {
    loadedByFile.set(file, Math.min(file.size, Math.max(0, loadedBytes)));
    const loaded = Array.from(loadedByFile.values()).reduce((total, value) => total + value, 0);
    opts?.onTransferProgress?.({
      loadedBytes: loaded,
      totalBytes,
      percent: totalBytes > 0 ? Math.min(99, Math.round((loaded / totalBytes) * 100)) : 100,
      completed: false,
      totalIsEstimated: false,
    });
  };
  opts?.onTransferProgress?.(initialTransferProgress(totalBytes));

  const worker = async () => {
    while (queue.length > 0) {
      const f = queue.shift()!;
      try {
        results.push(await onFile(f, batchName, (progress) => {
          reportTransfer(f, progress.loadedBytes);
        }));
        reportTransfer(f, f.size);
        success++;
      } catch (error) {
        reportTransfer(f, f.size);
        failures.push({ file_name: f.name, reason: describeApiError(error, '上传失败') });
      }
      processed++;
      opts?.onProgress?.(processed, matched.length, success);
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(opts?.concurrency ?? 4, matched.length) }, () => worker()),
  );
  opts?.onTransferProgress?.(completedTransferProgress(totalBytes, totalBytes));

  return { total: matched.length, success, results, failures };
}

/** Soft-delete all files in a batch (folder). */
export async function deleteBatch(batchName: string): Promise<void> {
  await apiClient.delete(`/api/v1/files/batches/${encodeURIComponent(batchName)}`);
}

/** Download all files in a batch as a ZIP archive. */
export async function downloadBatchZip(
  batchName: string,
  onProgress?: TransferProgressHandler,
  signal?: AbortSignal,
): Promise<void> {
  return downloadBlob({
    url: `/api/v1/files/batches/${encodeURIComponent(batchName)}/download-zip`,
    fallbackName: `${batchName}.zip`,
    errorMessage: '批次下载失败',
    onProgress,
    signal,
  });
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
