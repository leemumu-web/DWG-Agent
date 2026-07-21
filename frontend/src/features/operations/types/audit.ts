export interface AuditLog {
  id: number;
  actor_user_id?: number | null;
  action: string;
  resource_type: string;
  resource_id?: number | null;
  ip_address?: string | null;
  user_agent?: string | null;
  before_json?: Record<string, unknown> | null;
  after_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}
