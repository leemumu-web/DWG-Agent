import { apiClient, type ApiEnvelope, type PageEnvelope } from '../../shared/api';
import type { OriginalDownload, Remnant, RemnantMaterial, RemnantSearch } from './types';

export async function listRemnantMaterials(): Promise<RemnantMaterial[]> {
  const response = await apiClient.get<ApiEnvelope<RemnantMaterial[]>>('/api/v1/remnant-materials');
  return response.data.data;
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
