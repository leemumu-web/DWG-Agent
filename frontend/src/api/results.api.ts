import { apiClient, type ApiEnvelope } from './client';
import type { AnalysisResult } from '../types/result';

export async function getResult(resultId: number) {
  const res = await apiClient.get<ApiEnvelope<AnalysisResult>>(`/api/v1/results/${resultId}`);
  return res.data.data;
}
