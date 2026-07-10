import { apiClient, fetchAllPages, type ApiEnvelope } from './client';
import { useAuthStore } from '../stores/auth.store';
import type { BatchInfo, ExcelPreviewResponse, StoredFile } from '../types/file';
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

function apiUrl(path: string): string {
  const base = import.meta.env.VITE_API_BASE_URL || '';
  return `${base}${path}`;
}

function authHeaders(): Record<string, string> {
  const token = useAuthStore.getState().accessToken;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function throwOnHttpError(res: Response): Promise<void> {
  if (res.ok) return;
  let body: Record<string, unknown> = {};
  try { body = await res.json(); } catch { /* use defaults */ }
  const detail = (body as { error?: { code?: string; message?: string } })?.error?.message
    || `HTTP ${res.status} ${res.statusText}`;
  throw new Error(detail);
}

/** fetch() wrapper with timeout and retry for upload/download operations. */
async function fetchWithTimeout(
  url: string,
  init: RequestInit & { timeout?: number },
  retries = 0,
): Promise<Response> {
  const timeout = init.timeout ?? 120_000;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const res = await fetch(url, { ...init, signal: controller.signal });
      return res;
    } catch (err) {
      if (attempt === retries) throw err;
      // Wait before retry (exponential backoff)
      await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
    } finally {
      clearTimeout(timer);
    }
  }
  throw new Error('fetch failed after retries');
}

export async function uploadFile(file: File, batchName?: string): Promise<StoredFile> {
  const form = new FormData();
  form.append('upload', file);
  let url = apiUrl('/api/v1/files');
  if (batchName) url += `?batch_name=${encodeURIComponent(batchName)}`;
  const res = await fetchWithTimeout(url, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
    timeout: 120_000,
  });
  await throwOnHttpError(res);
  const json = await res.json();
  return json.data as StoredFile;
}

export async function uploadFileAndConvert(file: File, batchName?: string) {
  const stored = await uploadFile(file, batchName);
  const res = await fetchWithTimeout(apiUrl('/api/v1/jobs'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ task_type: 'convert_dwg_to_dxf', precision_level: 'normal', params: { file_id: stored.id, batch_name: batchName || null } }),
    timeout: 30_000,
  });
  await throwOnHttpError(res);
  const json = await res.json();
  return { file: stored, job: json.data as Job };
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
  const url = apiUrl(`/api/v1/files/upload-zip?file_ext=${encodeURIComponent(fileExt)}`);
  const res = await fetchWithTimeout(url, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
    timeout: 300_000,  // 5 min for large archives
  });
  await throwOnHttpError(res);
  const json = await res.json();
  return json.data as ZipUploadResult;
}

export async function getFileDownloadUrl(fileId: number) {
  const res = await apiClient.get<ApiEnvelope<{ url: string; expires_in: number }>>(
    `/api/v1/files/${fileId}/download-url`
  );
  return res.data.data;
}

/** Download a file via the signed URL, using fetch() with auth headers
 *  because browser <a> clicks don't send Authorization headers. */
export async function downloadFile(fileId: number, filename: string): Promise<void> {
  const { url } = await getFileDownloadUrl(fileId);
  const fullUrl = apiUrl(url);
  const res = await fetchWithTimeout(fullUrl, { headers: authHeaders(), timeout: 120_000 }, 1);
  if (!res.ok) throw new Error(`Download failed: HTTP ${res.status}`);
  const blob = await res.blob();
  triggerBlobDownload(blob, filename);
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
  const res = await fetch(apiUrl('/api/v1/files/download-zip'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ file_ids: fileIds, formats, folder_name: folderName }),
  });
  if (!res.ok) {
    let msg = `下载失败: HTTP ${res.status}`;
    try {
      const b = await res.json();
      msg = (b as { error?: { message?: string } })?.error?.message || msg;
    } catch { /* use status text */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  triggerBlobDownload(blob, `${folderName}.zip`);
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
  const url = apiUrl(
    `/api/v1/files/batches/${encodeURIComponent(batchName)}/download-zip`,
  );
  const res = await fetchWithTimeout(url, { headers: authHeaders(), timeout: 300_000 }, 1);
  if (!res.ok) {
    let msg = `下载失败: HTTP ${res.status}`;
    try {
      const b = await res.json();
      msg = (b as { error?: { message?: string } })?.error?.message || msg;
    } catch { /* use status text */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  triggerBlobDownload(blob, `${batchName}.zip`);
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
