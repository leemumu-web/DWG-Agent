import { apiClient, type PageEnvelope } from './client';
import type { AuditLog } from '../types/audit';

export async function listAuditLogs() {
  const res = await apiClient.get<PageEnvelope<AuditLog>>('/api/v1/audit-logs');
  return res.data.data;
}
