import { apiClient, type PageEnvelope } from './client';
import type { Drawing } from '../types/drawing';

export async function listDrawings() {
  const res = await apiClient.get<PageEnvelope<Drawing>>('/api/v1/drawings');
  return res.data.data;
}
