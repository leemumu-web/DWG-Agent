import { apiClient, type ApiEnvelope, type PageEnvelope } from './client';
import type { AuditLog } from '../types/audit';

/** Audit logs are capped at the most recent 200 on the backend (limit(200)),
 *  so a single page_size=200 fetch returns everything available. */
export async function listAuditLogs() {
  const res = await apiClient.get<PageEnvelope<AuditLog>>('/api/v1/audit-logs', {
    params: { page_size: 200 },
  });
  return res.data.data;
}

export async function getAuditLog(auditLogId: number) {
  const res = await apiClient.get<ApiEnvelope<AuditLog>>(`/api/v1/audit-logs/${auditLogId}`);
  return res.data.data;
}
