import { apiClient, type ApiEnvelope, type PageEnvelope } from '../../shared/api';
import type {
  ImportConfirmationResult,
  OriginalDownload,
  Remnant,
  RemnantImportBatch,
  RemnantImportItem,
  RemnantMaterial,
  RemnantSearch,
} from './types';

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

export async function resolveOrCreateRemnantMaterial(code: string): Promise<{
  material: RemnantMaterial;
  created: boolean;
}> {
  const response = await apiClient.post<ApiEnvelope<{
    material: RemnantMaterial;
    created: boolean;
  }>>('/api/v1/remnant-materials/resolve-or-create', { code });
  return response.data.data;
}

export async function updateRemnantMaterial(
  materialId: number,
  payload: { family_code?: string; enabled?: boolean },
): Promise<RemnantMaterial> {
  const response = await apiClient.patch<ApiEnvelope<RemnantMaterial>>(`/api/v1/remnant-materials/${materialId}`, payload);
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
  payload: { thickness_mm: string; material_id: number; project_no: string; parts: string[] },
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
  const response = await apiClient.get<Blob>(prepared.url, { responseType: 'blob' });
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

export async function getRemnantImportBatch(batchId: number): Promise<RemnantImportBatch> {
  const response = await apiClient.get<ApiEnvelope<RemnantImportBatch>>(
    `/api/v1/remnant-import-batches/${batchId}`,
  );
  return response.data.data;
}

export async function updateRemnantImportItem(
  itemId: number,
  payload: { thickness_mm?: string; material_id?: number; project_no?: string; parts?: string[] },
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

export async function retryRemnantImportItem(itemId: number): Promise<void> {
  await apiClient.post(`/api/v1/remnant-import-items/${itemId}/retry`);
}

export async function cancelRemnantImportBatch(batchId: number): Promise<void> {
  await apiClient.post(`/api/v1/remnant-import-batches/${batchId}/cancel`);
}

export async function confirmRemnantImportItems(itemIds: number[]): Promise<ImportConfirmationResult> {
  const response = await apiClient.post<ApiEnvelope<ImportConfirmationResult>>(
    '/api/v1/remnant-import-items/bulk-confirm',
    { item_ids: itemIds },
  );
  return response.data.data;
}
