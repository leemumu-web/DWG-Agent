import { apiClient, type ApiEnvelope, type PageEnvelope } from '../../shared/api';
import type {
  BulkArchiveResult,
  ImportConfirmationResult,
  OriginalDownload,
  Remnant,
  RemnantGlobalSearch,
  RemnantImportBatch,
  RemnantImportItem,
  RemnantMaterial,
  RemnantSearch,
} from './types';

export async function bulkArchiveRemnants(remnantIds: number[]): Promise<BulkArchiveResult> {
  const response = await apiClient.post<ApiEnvelope<BulkArchiveResult>>(
    '/api/v1/remnants/bulk-archive',
    { remnant_ids: remnantIds },
  );
  return response.data.data;
}

export async function listRemnantMaterials(enabledOnly = true): Promise<RemnantMaterial[]> {
  const response = await apiClient.get<ApiEnvelope<RemnantMaterial[]>>('/api/v1/remnant-materials', {
    params: enabledOnly ? undefined : { enabled_only: false },
  });
  return response.data.data;
}

export async function createRemnantMaterial(payload: { code: string; family_code: string }): Promise<RemnantMaterial> {
  const response = await apiClient.post<ApiEnvelope<RemnantMaterial>>('/api/v1/remnant-materials', payload);
  return response.data.data;
}

export async function resolveOrCreateRemnantMaterial(itemId: number, code: string): Promise<{
  material: RemnantMaterial;
  created: boolean;
}> {
  const response = await apiClient.post<ApiEnvelope<{
    material: RemnantMaterial;
    created: boolean;
  }>>(`/api/v1/remnant-import-items/${itemId}/resolve-material`, { code });
  return response.data.data;
}

export async function updateRemnantMaterial(
  materialId: number,
  payload: { family_code?: string; enabled?: boolean },
): Promise<RemnantMaterial> {
  const response = await apiClient.patch<ApiEnvelope<RemnantMaterial>>(`/api/v1/remnant-materials/${materialId}`, payload);
  return response.data.data;
}

export async function setRemnantMaterialStatus(
  materialId: number,
  enabled: boolean,
): Promise<{ material: RemnantMaterial; message: string }> {
  const response = await apiClient.patch<ApiEnvelope<{ material: RemnantMaterial; message: string }>>(
    `/api/v1/remnant-materials/${materialId}/status`,
    { enabled },
  );
  return response.data.data;
}

export async function replaceRemnantMaterialAliases(materialId: number, aliases: string[]): Promise<void> {
  await apiClient.put(`/api/v1/remnant-materials/${materialId}/aliases`, { aliases });
}

export async function searchRemnants(search: RemnantSearch): Promise<PageEnvelope<Remnant>> {
  const response = await apiClient.get<PageEnvelope<Remnant>>('/api/v1/remnants', {
    params: {
      material_id: search.materialId,
      thickness_mm: search.thicknessMm,
      include_family: search.includeFamily,
      statuses: search.statuses,
      page: search.page,
      page_size: 20,
    },
    paramsSerializer: { indexes: null },
  });
  return response.data;
}

export async function listAllRemnants(search: RemnantGlobalSearch): Promise<PageEnvelope<Remnant>> {
  const response = await apiClient.get<PageEnvelope<Remnant>>('/api/v1/remnants/all', {
    params: {
      material_id: search.materialId,
      thickness_mm: search.thicknessMm,
      statuses: search.statuses,
      project: search.project,
      project_secondary: search.projectSecondary,
      storage_location: search.storageLocation,
      remark_1: search.remark1,
      remark_2: search.remark2,
      part: search.part,
      sort: search.sort,
      page: search.page,
      page_size: 20,
    },
    paramsSerializer: { indexes: null },
  });
  return response.data;
}

export async function exportAllRemnants(): Promise<void> {
  const response = await apiClient.get<Blob>('/api/v1/remnants/export.xlsx', { responseType: 'blob' });
  const disposition = String(response.headers['content-disposition'] ?? '');
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const fileName = encodedName ? decodeURIComponent(encodedName) : '余料库.xlsx';
  const href = URL.createObjectURL(response.data);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(href);
}

export async function getRemnant(remnantId: number): Promise<Remnant> {
  const response = await apiClient.get<ApiEnvelope<Remnant>>(`/api/v1/remnants/${remnantId}`);
  return response.data.data;
}

export async function reserveRemnant(remnant: Remnant): Promise<Remnant> {
  const response = await apiClient.post<ApiEnvelope<Remnant>>(
    `/api/v1/remnants/${remnant.id}/reserve`,
    { version: remnant.version },
  );
  return response.data.data;
}

export async function releaseRemnant(remnantId: number): Promise<Remnant> {
  const response = await apiClient.post<ApiEnvelope<Remnant>>(`/api/v1/remnants/${remnantId}/release`);
  return response.data.data;
}

export async function markRemnantUsed(remnantId: number): Promise<Remnant> {
  const response = await apiClient.post<ApiEnvelope<Remnant>>(`/api/v1/remnants/${remnantId}/mark-used`);
  return response.data.data;
}

export async function updateRemnant(
  remnantId: number,
  payload: {
    thickness_mm: string;
    material_id: number;
    project_no: string;
    project_no_secondary?: string;
    storage_location?: string;
    remark_1?: string;
    remark_2?: string;
    parts: string[];
  },
): Promise<Remnant> {
  const response = await apiClient.patch<ApiEnvelope<Remnant>>(`/api/v1/remnants/${remnantId}`, payload);
  return response.data.data;
}

export async function archiveRemnant(remnantId: number): Promise<Remnant> {
  const response = await apiClient.post<ApiEnvelope<Remnant>>(`/api/v1/remnants/${remnantId}/archive`);
  return response.data.data;
}

export async function getOriginalDownload(remnantId: number): Promise<OriginalDownload> {
  const response = await apiClient.get<ApiEnvelope<OriginalDownload>>(
    `/api/v1/remnants/${remnantId}/original-download`,
  );
  return response.data.data;
}

export async function downloadOriginal(remnantId: number): Promise<void> {
  const prepared = await getOriginalDownload(remnantId);
  const response = await apiClient.get<Blob>(prepared.url, {
    responseType: 'blob',
    validateStatus: () => true,
  });
  if (response.status < 200 || response.status >= 300) throw new Error('原图下载失败');
  const href = URL.createObjectURL(response.data);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = prepared.file_name;
  anchor.click();
  URL.revokeObjectURL(href);
}

export async function createRemnantImportBatch(files: File[]): Promise<RemnantImportBatch> {
  const form = new FormData();
  files.forEach((file) => form.append('files', file));
  const response = await apiClient.post<ApiEnvelope<RemnantImportBatch>>(
    '/api/v1/remnant-import-batches',
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return response.data.data;
}

export async function deleteArchivedRemnant(remnantId: number): Promise<void> {
  await apiClient.delete(`/api/v1/remnants/${remnantId}`);
}

export interface AutoImportFile {
  file: File;
  relativePath: string;
}

export async function createAutoRemnantImportBatch(
  entries: AutoImportFile[],
  projectNo: string,
  folderName?: string,
): Promise<RemnantImportBatch> {
  const form = new FormData();
  entries.forEach(({ file, relativePath }) => {
    form.append('files', file);
    form.append('relative_paths', relativePath);
  });
  form.append('project_no', projectNo);
  if (folderName) form.append('folder_name', folderName);
  const response = await apiClient.post<ApiEnvelope<RemnantImportBatch>>(
    '/api/v1/remnant-import-batches/auto',
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return response.data.data;
}

export async function getRemnantImportBatch(batchId: number): Promise<RemnantImportBatch> {
  const response = await apiClient.get<ApiEnvelope<RemnantImportBatch>>(
    `/api/v1/remnant-import-batches/${batchId}`,
  );
  return response.data.data;
}

export async function updateRemnantImportItem(
  itemId: number,
  payload: {
    thickness_mm?: string;
    material_id?: number;
    project_no?: string;
    project_no_secondary?: string;
    storage_location?: string;
    remark_1?: string;
    remark_2?: string;
    parts?: string[];
  },
): Promise<RemnantImportItem> {
  const response = await apiClient.patch<ApiEnvelope<RemnantImportItem>>(
    `/api/v1/remnant-import-items/${itemId}`,
    payload,
  );
  return response.data.data;
}

export async function bulkApplyThickness(
  batchId: number,
  itemIds: number[],
  thicknessMm: string,
): Promise<number[]> {
  const response = await apiClient.post<ApiEnvelope<{ updated_item_ids: number[] }>>(
    `/api/v1/remnant-import-batches/${batchId}/bulk-thickness`,
    { item_ids: itemIds, thickness_mm: thicknessMm },
  );
  return response.data.data.updated_item_ids;
}

export async function bulkApplyProject(
  batchId: number,
  itemIds: number[],
  projectNo: string,
): Promise<number[]> {
  const response = await apiClient.post<ApiEnvelope<{ updated_item_ids: number[] }>>(
    `/api/v1/remnant-import-batches/${batchId}/bulk-project`,
    { item_ids: itemIds, project_no: projectNo },
  );
  return response.data.data.updated_item_ids;
}

export async function bulkApplyOptionalMetadata(
  batchId: number,
  itemIds: number[],
  payload: {
    project_no_secondary?: string;
    storage_location?: string;
    remark_1?: string;
    remark_2?: string;
  },
): Promise<number[]> {
  const response = await apiClient.post<ApiEnvelope<{ updated_item_ids: number[] }>>(
    `/api/v1/remnant-import-batches/${batchId}/bulk-optional-metadata`,
    { item_ids: itemIds, ...payload },
  );
  return response.data.data.updated_item_ids;
}

export async function retryRemnantImportItem(itemId: number): Promise<void> {
  await apiClient.post(`/api/v1/remnant-import-items/${itemId}/retry`);
}

export async function cancelRemnantImportBatch(batchId: number): Promise<void> {
  await apiClient.post(`/api/v1/remnant-import-batches/${batchId}/cancel`);
}

export async function cancelRemnantImportItem(itemId: number): Promise<RemnantImportItem> {
  const response = await apiClient.post<ApiEnvelope<RemnantImportItem>>(
    `/api/v1/remnant-import-items/${itemId}/cancel`,
  );
  return response.data.data;
}

export async function confirmRemnantImportItems(itemIds: number[]): Promise<ImportConfirmationResult> {
  const response = await apiClient.post<ApiEnvelope<ImportConfirmationResult>>(
    '/api/v1/remnant-import-items/bulk-confirm',
    { item_ids: itemIds },
  );
  return response.data.data;
}
