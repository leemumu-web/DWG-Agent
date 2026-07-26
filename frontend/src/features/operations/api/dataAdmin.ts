import { apiClient, type ApiEnvelope } from '../../../shared/api';
import type {
  DataAdminFile,
  DataAdminOverview,
  StorageObject,
  StorageObjectTree,
} from '../types/dataAdmin';

export async function getDataAdminOverview() {
  const response = await apiClient.get<ApiEnvelope<DataAdminOverview>>('/api/v1/data-admin/overview');
  return response.data.data;
}

export async function getDataAdminFile(fileId: number) {
  const response = await apiClient.get<ApiEnvelope<DataAdminFile>>(`/api/v1/data-admin/files/${fileId}`);
  return response.data.data;
}

export async function getStorageObjectTree(params: {
  bucket: string;
  prefix?: string;
}) {
  const response = await apiClient.get<ApiEnvelope<StorageObjectTree>>(
    '/api/v1/data-admin/objects/tree',
    { params },
  );
  return response.data.data;
}

export async function uploadDataAdminObject(file: File) {
  const body = new FormData();
  body.append('upload', file);
  const response = await apiClient.post<ApiEnvelope<DataAdminFile>>(
    '/api/v1/files',
    body,
    { headers: { 'Idempotency-Key': crypto.randomUUID() } },
  );
  return response.data.data;
}

export async function deleteDataAdminObject(bucket: string, storageKey: string) {
  await apiClient.delete('/api/v1/data-admin/objects', {
    params: { bucket, storage_key: storageKey },
  });
}

export async function moveDataAdminObject(payload: {
  bucket: string;
  storage_key: string;
  target_bucket: string;
  target_storage_key: string;
}) {
  const response = await apiClient.post<ApiEnvelope<DataAdminFile>>(
    '/api/v1/data-admin/objects/moves',
    payload,
  );
  return response.data.data;
}
