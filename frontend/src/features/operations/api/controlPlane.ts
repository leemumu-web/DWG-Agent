import { apiClient, type ApiEnvelope, type PageEnvelope } from '../../../shared/api';

export interface ControlPlaneOverview {
  checked_at: string;
  broker: { kind: string; url_scheme: string; ready_count_source: string; limitations: string[] };
  queues: Array<{ name: string; business_jobs: Record<string, number>; broker_ready_messages: number | null; mode: 'active' | 'contract_only' }>;
  workers: Array<{ id: number; worker_name: string; hostname: string | null; process_id: number | null; queues: string[]; concurrency: number | null; status: string; started_at: string | null; last_seen_at: string; stopped_at: string | null }>;
  summary: { registered_workers: number; online_workers: number; stale_workers: number; unread_messages: number };
  implementation: Record<string, 'pending'>;
}
export interface ControlPlaneEvent { id: number; source: string; direction: string; event_type: string; severity: string; correlation_id: string | null; target_kind: string | null; target_id: string | null; payload: Record<string, unknown> | null; message: string | null; created_at: string; }
export interface PlatformMessage { id: number; severity: string; category: string; title: string; body: string | null; status: string; created_at: string; }
export interface WindowsNodeContract { version: string; status: 'pending'; transport: string; endpoints: Array<{ method: string; path: string; purpose: string }>; not_available: string[]; }

export async function getControlPlaneOverview() { return (await apiClient.get<ApiEnvelope<ControlPlaneOverview>>('/api/v1/control-plane/overview')).data.data; }
export async function listControlPlaneEvents() { return (await apiClient.get<PageEnvelope<ControlPlaneEvent>>('/api/v1/control-plane/events?page=1&page_size=20')).data; }
export async function listPlatformMessages() { return (await apiClient.get<PageEnvelope<PlatformMessage>>('/api/v1/control-plane/messages?page=1&page_size=20')).data; }
export async function markPlatformMessageRead(id: number) { return (await apiClient.patch<ApiEnvelope<PlatformMessage>>(`/api/v1/control-plane/messages/${id}/read`)).data.data; }
export async function getWindowsNodeContract() { return (await apiClient.get<ApiEnvelope<WindowsNodeContract>>('/api/v1/control-plane/contracts/windows-node-agent')).data.data; }
export async function queueStaleJobReconciliation() { return (await apiClient.post<ApiEnvelope<{ operation: string; queue: string; task_id: string }>>('/api/v1/control-plane/maintenance/reconcile-stale-jobs')).data.data; }
