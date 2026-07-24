import { apiClient, type ApiEnvelope } from '../../shared/api';

export async function getResultDownloadUrl(resultId: number) {
  const res = await apiClient.get<ApiEnvelope<{ url: string; expires_in: number }>>(
    `/api/v1/workflows/results/${resultId}/download-url`
  );
  return res.data.data;
}
