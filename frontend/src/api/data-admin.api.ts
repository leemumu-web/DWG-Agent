import { apiClient, type ApiEnvelope, type PageEnvelope } from './client';
import type {
  DataAdminFile,
  DataAdminOverview,
  FileTransfer,
  RemediationAction,
  RemediationPreview,
  RemediationResult,
  StorageObject,
  StorageScanFinding,
  StorageScanRun,
} from '../types/data-admin';

export interface PageQuery {
  page: number;
  page_size: number;
}

export async function getDataAdminOverview() {
  const response = await apiClient.get<ApiEnvelope<DataAdminOverview>>('/api/v1/data-admin/overview');
  return response.data.data;
}

export async function listDataAdminFiles(params: PageQuery & {
  search?: string;
  status?: string;
  bucket?: string;
  file_ext?: string;
}) {
  const response = await apiClient.get<PageEnvelope<DataAdminFile>>('/api/v1/data-admin/files', { params });
  return response.data;
}

export async function getDataAdminFile(fileId: number) {
  const response = await apiClient.get<ApiEnvelope<DataAdminFile>>(`/api/v1/data-admin/files/${fileId}`);
  return response.data.data;
}

export async function listStorageObjects(params: {
  bucket: string;
  prefix?: string;
  cursor?: string;
  page_size: number;
}) {
  const response = await apiClient.get<ApiEnvelope<StorageObject[]> & { cursor: { next?: string | null } }>(
    '/api/v1/data-admin/objects',
    { params },
  );
  return response.data;
}

export async function listFileTransfers(params: PageQuery & {
  direction?: string;
  status?: string;
  operation?: string;
  file_id?: number;
}) {
  const response = await apiClient.get<PageEnvelope<FileTransfer>>('/api/v1/data-admin/transfers', { params });
  return response.data;
}

export async function getFileTransfer(transferUid: string) {
  const response = await apiClient.get<ApiEnvelope<FileTransfer>>(`/api/v1/data-admin/transfers/${transferUid}`);
  return response.data.data;
}

export async function startStorageScan(scopeBucket?: string) {
  const response = await apiClient.post<ApiEnvelope<StorageScanRun>>('/api/v1/data-admin/scans', {
    scope_bucket: scopeBucket || null,
  });
  return response.data.data;
}

export async function getStorageScan(scanId: number) {
  const response = await apiClient.get<ApiEnvelope<StorageScanRun>>(`/api/v1/data-admin/scans/${scanId}`);
  return response.data.data;
}

export async function listStorageScans(params: PageQuery & {
  status?: string;
  scope_bucket?: string;
}) {
  const response = await apiClient.get<PageEnvelope<StorageScanRun>>('/api/v1/data-admin/scans', { params });
  return response.data;
}

export async function listStorageScanFindings(scanId: number, params: PageQuery & {
  finding_type?: string;
  resolution_status?: string;
}) {
  const response = await apiClient.get<PageEnvelope<StorageScanFinding>>(
    `/api/v1/data-admin/scans/${scanId}/findings`,
    { params },
  );
  return response.data;
}

export async function previewStorageRemediation(payload: {
  finding_ids: number[];
  action: RemediationAction;
  metadata?: Record<string, string>;
}) {
  const response = await apiClient.post<ApiEnvelope<RemediationPreview>>(
    '/api/v1/data-admin/remediations/preview',
    payload,
  );
  return response.data.data;
}

export async function executeStorageRemediation(payload: {
  preview_token: string;
  idempotency_key: string;
  confirmation_word?: string;
}) {
  const response = await apiClient.post<ApiEnvelope<RemediationResult>>(
    '/api/v1/data-admin/remediations/execute',
    payload,
  );
  return response.data.data;
}
