import { apiClient, type ApiEnvelope } from './client';

export interface SystemHealth {
  status: string;
  redis: boolean;
  features: {
    agent: boolean;
    dxf_pipeline: boolean;
    cad_worker: boolean;
  };
  storage_backend: string;
}

export async function getSystemHealth() {
  const res = await apiClient.get<ApiEnvelope<SystemHealth>>('/api/v1/system/health');
  return res.data.data;
}
