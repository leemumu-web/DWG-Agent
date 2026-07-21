import { apiClient, type ApiEnvelope, type PageEnvelope } from '../../../shared/api';
import type { AuditLog } from '../types/audit';

/** Load the most recent 200 entries for the dashboard view.
 *  The backend keeps an exact total and supports SQL pagination beyond this view. */
export async function listAuditLogs() {
  const res = await apiClient.get<PageEnvelope<AuditLog>>('/api/v1/audit-logs', {
    params: { page_size: 200 },
  });
  return res.data.data;
}

export interface AuditLogListParams {
  page: number;
  page_size: number;
  action_domain?: string;
  search?: string;
}

export async function listAuditLogsPage(params: AuditLogListParams) {
  const res = await apiClient.get<PageEnvelope<AuditLog>>('/api/v1/audit-logs', { params });
  return res.data;
}

export async function getAuditLog(auditLogId: number) {
  const res = await apiClient.get<ApiEnvelope<AuditLog>>(`/api/v1/audit-logs/${auditLogId}`);
  return res.data.data;
}
