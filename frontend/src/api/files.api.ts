import { apiClient, type PageEnvelope } from './client';
import type { StoredFile } from '../types/file';

export async function listFiles() {
  const res = await apiClient.get<PageEnvelope<StoredFile>>('/api/v1/files');
  return res.data.data;
}

export async function uploadDwg(file: File) {
  const form = new FormData();
  form.append('upload', file);
  const res = await apiClient.post('/api/v1/files', form, { headers: { 'Content-Type': 'multipart/form-data' } });
  return res.data.data as StoredFile;
}
