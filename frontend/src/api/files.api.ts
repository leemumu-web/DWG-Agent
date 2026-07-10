import axios from 'axios';
import { apiClient, fetchAllPages, type ApiEnvelope } from './client';
import { useAuthStore } from '../stores/auth.store';
import type { BatchInfo, DxfPreviewResponse, ExcelPreviewResponse, StoredFile } from '../types/file';
import type { Job } from '../types/job';

/** Fetch ALL files, optionally filtered by batch_name and/or file_ext. */
export async function listFiles(batchName?: string, fileExt?: string) {
  const params: Record<string, unknown> = {};
  if (batchName) params.batch_name = batchName;
  if (fileExt) params.file_ext = fileExt;
  return fetchAllPages<StoredFile>('/api/v1/files', params);
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
  if (!axios.isAxiosError(error)) {
    return error instanceof Error ? error : new Error('下载失败');
  }
  let body = error.response?.data as unknown;
  if (body instanceof Blob) {
    try {
      body = JSON.parse(await body.text()) as unknown;
    } catch {
      body = undefined;
    }
  }
  const message = (body as { error?: { message?: string } } | undefined)?.error?.message;
  return new Error(message || `下载失败: HTTP ${error.response?.status ?? '网络错误'}`);
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
  const res = await apiClient.post<Blob>('/api/v1/files/download-zip', {
    file_ids: fileIds,
    formats,
    folder_name: folderName,
  }, {
    responseType: 'blob',
    timeout: 300_000,
  });
  triggerBlobDownload(res.data, `${folderName}.zip`);
}

/** Soft-delete multiple files at once. */
export async function bulkDeleteFiles(fileIds: number[]): Promise<void> {
  await apiClient.post('/api/v1/files/bulk-delete', { file_ids: fileIds });
}

/** Upload a folder — process all matching files with max 3 concurrent uploads.
 *  The folder name becomes the batch_name for all files in it.
 *  @param fileExt  only upload files matching this extension (e.g. '.dwg', '.dxf')
 *  @param onFile   upload+convert callback: receives (file, batchName) → result */
export async function uploadFolder(
  files: File[],
  batchName: string,
  opts?: { fileExt?: string; onFile?: (file: File, batchName: string) => Promise<unknown> },
): Promise<{ total: number; success: number }> {
  const ext = opts?.fileExt || '.dwg';
  const onFile = opts?.onFile || ((f: File, bn: string) => uploadFileAndConvert(f, bn));
  const matched = files.filter((f) => f.name.toLowerCase().endsWith(ext));
  if (matched.length === 0) return { total: 0, success: 0 };

  const queue = [...matched];
  let success = 0;

  const worker = async () => {
    while (queue.length > 0) {
      const f = queue.shift()!;
      try {
        await onFile(f, batchName);
        success++;
      } catch { /* per-file failure, continue with others */ }
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(3, matched.length) }, () => worker()),
  );

  return { total: matched.length, success };
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

/** Fetch DXF file preview data (rendered PNG URL + metadata). */
export async function fetchDxfPreview(fileId: number): Promise<DxfPreviewResponse> {
  const res = await apiClient.get<ApiEnvelope<DxfPreviewResponse>>(
    `/api/v1/files/${fileId}/dxf-preview`,
  );
  return res.data.data;
}
