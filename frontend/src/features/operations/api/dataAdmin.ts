import { apiClient, type ApiEnvelope, type PageEnvelope } from '../../../shared/api';
import type {
  DataAdminFile,
  DataAdminOverview,
  DailyArchivePreview,
  DailyArchiveRun,
  FileTransfer,
  RemediationAction,
  RemediationPreview,
  RemediationResult,
  StorageObject,
  StorageObjectTree,
  StorageScanFinding,
  StorageScanRun,
  MySqlRow,
  MySqlTable,
  MySqlTableSummary,
} from '../types/dataAdmin';

export interface PageQuery {
  page: number;
  page_size: number;
}

export async function getDataAdminOverview() {
  const response = await apiClient.get<ApiEnvelope<DataAdminOverview>>('/api/v1/data-admin/overview');
  return response.data.data;
}

export async function listMySqlTables() {
  const response = await apiClient.get<ApiEnvelope<MySqlTableSummary[]>>(
    '/api/v1/data-admin/mysql/tables',
  );
  return response.data.data;
}

export async function getMySqlTable(tableName: string) {
  const response = await apiClient.get<ApiEnvelope<MySqlTable>>(
    `/api/v1/data-admin/mysql/tables/${encodeURIComponent(tableName)}`,
  );
  return response.data.data;
}

export async function listMySqlRows(tableName: string, params: PageQuery) {
  const response = await apiClient.get<PageEnvelope<MySqlRow>>(
    `/api/v1/data-admin/mysql/tables/${encodeURIComponent(tableName)}/rows`,
    { params },
  );
  return response.data;
}

export async function createMySqlRow(tableName: string, values: MySqlRow) {
  const response = await apiClient.post<ApiEnvelope<{ primary_key: MySqlRow }>>(
    `/api/v1/data-admin/mysql/tables/${encodeURIComponent(tableName)}/rows`,
    { values },
  );
  return response.data.data;
}

export async function updateMySqlRow(
  tableName: string,
  primaryKey: MySqlRow,
  values: MySqlRow,
) {
  const response = await apiClient.patch<ApiEnvelope<MySqlRow>>(
    `/api/v1/data-admin/mysql/tables/${encodeURIComponent(tableName)}/rows`,
    { primary_key: primaryKey, values },
  );
  return response.data.data;
}

export async function deleteMySqlRow(tableName: string, primaryKey: MySqlRow) {
  const response = await apiClient.delete<ApiEnvelope<{ deleted: boolean }>>(
    `/api/v1/data-admin/mysql/tables/${encodeURIComponent(tableName)}/rows`,
    { data: { primary_key: primaryKey } },
  );
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

export async function previewDailyArchive(payload: {
  archive_date?: string;
  scope_bucket?: string;
}) {
  const response = await apiClient.post<ApiEnvelope<DailyArchivePreview>>(
    '/api/v1/data-admin/daily-archives/preview',
    {
      archive_date: payload.archive_date || null,
      scope_bucket: payload.scope_bucket || null,
    },
  );
  return response.data.data;
}

export async function createDailyArchive(payload: {
  preview_token: string;
  idempotency_key: string;
}) {
  const response = await apiClient.post<ApiEnvelope<DailyArchiveRun>>(
    '/api/v1/data-admin/daily-archives',
    payload,
  );
  return response.data.data;
}

export async function listDailyArchives(params: PageQuery & {
  status?: string;
  scope_bucket?: string;
  archive_date?: string;
}) {
  const response = await apiClient.get<PageEnvelope<DailyArchiveRun>>(
    '/api/v1/data-admin/daily-archives',
    { params },
  );
  return response.data;
}

export async function getDailyArchive(archiveId: number) {
  const response = await apiClient.get<ApiEnvelope<DailyArchiveRun>>(
    `/api/v1/data-admin/daily-archives/${archiveId}`,
  );
  return response.data.data;
}
