import { apiClient, type ApiEnvelope, type PageEnvelope } from './client';
import { useAuthStore } from '../stores/auth.store';
import type { StoredFile } from '../types/file';
import type { Job } from '../types/job';

export async function listFiles() {
  const res = await apiClient.get<PageEnvelope<StoredFile>>('/api/v1/files');
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
  console.error('[upload] server error', res.status, body);
  throw new Error(detail);
}

export async function uploadDwg(file: File): Promise<StoredFile> {
  const form = new FormData();
  form.append('upload', file);
  console.log('[upload] starting upload for', file.name, 'size', file.size);
  const res = await fetch(apiUrl('/api/v1/files'), {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });
  await throwOnHttpError(res);
  const json = await res.json();
  console.log('[upload] success', json.data?.id);
  return json.data as StoredFile;
}

export async function uploadDwgAndConvert(file: File) {
  const stored = await uploadDwg(file);
  console.log('[upload] creating DXF job for file', stored.id);
  const res = await fetch(apiUrl('/api/v1/jobs'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ task_type: 'convert_dwg_to_dxf', precision_level: 'normal', params: { file_id: stored.id } }),
  });
  await throwOnHttpError(res);
  const json = await res.json();
  console.log('[upload] job created', json.data?.id);
  return { file: stored, job: json.data as Job };
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
  const res = await fetch(fullUrl, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Download failed: HTTP ${res.status}`);
  const blob = await res.blob();
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
