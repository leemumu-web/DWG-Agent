import { apiClient, fetchAllPages, type ApiEnvelope, type PageEnvelope } from './client';
import type { Drawing } from '../types/drawing';

export interface DrawingVersion {
  id: number;
  drawing_id: number;
  file_id: number;
  version_no: number;
  source?: string | null;
  created_by?: number | null;
}

export async function listDrawings() {
  return fetchAllPages<Drawing>('/api/v1/drawings');
}

export async function getDrawing(drawingId: number) {
  const res = await apiClient.get<ApiEnvelope<Drawing>>(`/api/v1/drawings/${drawingId}`);
  return res.data.data;
}

export async function listDrawingVersions(drawingId: number) {
  const res = await apiClient.get<PageEnvelope<DrawingVersion>>(
    `/api/v1/drawings/${drawingId}/versions`,
    { params: { page_size: 200 } },
  );
  return res.data.data;
}
