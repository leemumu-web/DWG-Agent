import { apiClient, type PageEnvelope } from './client';
import type { AnalysisResult } from '../types/result';

export async function listPendingReviews() {
  const res = await apiClient.get<PageEnvelope<AnalysisResult>>('/api/v1/reviews/pending');
  return res.data.data;
}
