import { apiClient, fetchAllPages, type ApiEnvelope } from '../../shared/api';
import type { AnalysisResult } from '../jobs';

export interface ReviewRecord {
  id: number;
  result_id: number;
  reviewer_id?: number | null;
  decision: 'approved' | 'rejected' | 'needs_revision';
  comment?: string | null;
  created_at: string;
}

export async function listPendingReviews() {
  return fetchAllPages<AnalysisResult>('/api/v1/workflows/reviews/pending');
}

export async function getResult(resultId: number) {
  const res = await apiClient.get<ApiEnvelope<AnalysisResult>>(`/api/v1/workflows/results/${resultId}`);
  return res.data.data;
}

export async function submitReview(
  resultId: number,
  payload: { decision: 'approved' | 'rejected' | 'needs_revision'; comment?: string },
) {
  const res = await apiClient.post<ApiEnvelope<ReviewRecord>>(
    `/api/v1/workflows/results/${resultId}/reviews`,
    payload,
  );
  return res.data.data;
}

export async function listResultReviews(resultId: number) {
  const res = await apiClient.get<ApiEnvelope<ReviewRecord[]>>(
    `/api/v1/workflows/results/${resultId}/reviews`,
  );
  return res.data.data;
}
