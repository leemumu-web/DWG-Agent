import { apiClient, type ApiEnvelope } from '../../../shared/api';

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

export interface InfrastructureOverview {
  status: 'ok' | 'degraded';
  checked_at: string;
  database: {
    status: 'ok' | 'error';
    engine: string;
    database: string;
    latency_ms: number;
    table_count: number | null;
    pool: { size: number; max_overflow: number; recycle_seconds: number };
  };
  storage: {
    status: 'ok' | 'error';
    backend: string;
    latency_ms: number;
    buckets: Array<{ name: string; tracked_files: number; object_count: number | null }>;
  };
  catalog: { available_files: number; tracked_bytes: number; extensions: Record<string, number> };
  capacity: {
    status: 'ok' | 'warning' | 'critical' | 'unknown';
    backend: string;
    disk_total_bytes: number | null;
    disk_used_bytes: number | null;
    disk_free_bytes: number | null;
    used_percent: number | null;
    reason: string | null;
    checked_at: string;
  };
  recovery: { consistency_rule: string; automated_backup: boolean };
}

export async function getSystemHealth() {
  const res = await apiClient.get<ApiEnvelope<SystemHealth>>('/api/v1/system/health');
  return res.data.data;
}

export async function getInfrastructureOverview() {
  const res = await apiClient.get<ApiEnvelope<InfrastructureOverview>>('/api/v1/system/infrastructure');
  return res.data.data;
}
