export interface DataAdminOverview {
  status: 'ok' | 'degraded';
  environment: {
    app_env: string;
    database_engine: string;
    database: string;
    storage_backend: string;
  };
  database: { status: 'ok' | 'error' };
  storage: {
    status: 'ok' | 'error';
    capacity: {
      status: 'ok' | 'warning' | 'critical' | 'unknown';
      total_bytes: number | null;
      used_bytes: number | null;
      free_bytes: number | null;
      used_percent: number | null;
      reason: string | null;
      checked_at: string | null;
    };
  };
  catalog: {
    available_files: number;
    deleted_files: number;
    tracked_bytes: number;
  };
  transfers_today: {
    inbound_succeeded: number;
    outbound_succeeded: number;
    attention_required: number;
  };
  latest_scan: StorageScanRun | null;
}

export interface DataAdminFile {
  id: number;
  bucket: string;
  storage_key: string;
  original_name: string;
  file_ext: string;
  content_type?: string | null;
  size_bytes: number;
  sha256: string;
  md5?: string | null;
  batch_name?: string | null;
  uploaded_by?: number | null;
  status: string;
  deleted_at?: string | null;
  purged_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface StorageObject {
  bucket: string;
  storage_key: string;
  size_bytes: number;
  last_modified?: string | null;
  registered: boolean;
  file_id?: number | null;
  file_status?: string | null;
}

export interface StorageObjectTree {
  bucket: string;
  prefix: string;
  folders: Array<{ name: string; prefix: string }>;
  objects: StorageObject[];
  truncated: boolean;
}

export interface FileTransfer {
  transfer_uid: string;
  direction: 'inbound' | 'outbound' | 'internal';
  operation: string;
  status: 'prepared' | 'in_progress' | 'succeeded' | 'failed' | 'cancelled' | 'compensation_required';
  file_id?: number | null;
  batch_ref?: string | null;
  actor_user_id?: number | null;
  request_id: string;
  bucket?: string | null;
  storage_key?: string | null;
  original_name?: string | null;
  expected_bytes?: number | null;
  transferred_bytes: number;
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
}

export interface StorageScanRun {
  id: number;
  backend: string;
  scope_bucket?: string | null;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  actor_user_id?: number | null;
  scanned_files: number;
  scanned_objects: number;
  consistent_count: number;
  retained_deleted_count: number;
  missing_object_count: number;
  untracked_object_count: number;
  size_mismatch_count: number;
  error_count: number;
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string;
}

export interface StorageScanFinding {
  id: number;
  finding_type: 'missing_object' | 'untracked_object' | 'size_mismatch' | 'retained_deleted';
  bucket: string;
  storage_key: string;
  file_id?: number | null;
  file_status?: string | null;
  database_size_bytes?: number | null;
  object_size_bytes?: number | null;
  object_modified_at?: string | null;
  resolution_status: string;
  resolution_action?: string | null;
}

export type RemediationAction = 'restore' | 'register_existing' | 'soft_delete_missing' | 'purge_untracked';

export interface RemediationPreview {
  action: RemediationAction;
  finding_ids: number[];
  count: number;
  total_bytes: number;
  risk: string;
  expires_at: string;
  confirmation_word?: string | null;
  token: string;
}

export interface RemediationResult {
  transfer_uid: string;
  action: RemediationAction;
  status: string;
  count: number;
  file_ids: number[];
}

export interface DailyArchivePreview {
  archive_date: string;
  timezone: string;
  scope_bucket?: string | null;
  window_start: string;
  window_end: string;
  file_count: number;
  total_bytes: number;
  excluded_archive_files: number;
  bucket_counts: Record<string, number>;
  format_counts: Record<string, number>;
  source_manifest_sha256: string;
  can_archive: boolean;
  block_reason?: string | null;
  expires_at: string;
  preview_token: string;
}

export interface DailyArchiveRun {
  id: number;
  archive_date: string;
  timezone: string;
  scope_bucket?: string | null;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  actor_user_id: number;
  source_manifest_sha256: string;
  file_count: number;
  total_bytes: number;
  bucket_counts: Record<string, number>;
  format_counts: Record<string, number>;
  task_id?: string | null;
  archive_file_id?: number | null;
  manifest_file_id?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
  reused: boolean;
}

export type MySqlValue =
  | string
  | number
  | boolean
  | null
  | MySqlValue[]
  | { [key: string]: MySqlValue };

export interface MySqlTableSummary {
  name: string;
  column_count: number;
  primary_key: string[];
}

export interface MySqlColumn {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
  autoincrement: boolean;
  default?: string | null;
  sensitive: boolean;
  required: boolean;
}

export interface MySqlTable {
  name: string;
  row_count: number;
  primary_key: string[];
  columns: MySqlColumn[];
}

export type MySqlRow = Record<string, MySqlValue>;
