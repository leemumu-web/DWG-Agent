import { apiClient, type ApiEnvelope } from './client';

export interface SystemHealth {
  status: string;
  features: {
    agent: boolean;
    dxf_pipeline: boolean;
    dxf2dwg_pipeline: boolean;
    dxf2excel_pipeline: boolean;
    excel_final_pipeline: boolean;
    cad_worker: boolean;
  };
  storage_backend: string;
}

export async function getSystemHealth() {
  const res = await apiClient.get<ApiEnvelope<SystemHealth>>('/api/v1/system/health');
  return res.data.data;
}
