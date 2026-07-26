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
    areas: StorageArea[];
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
  latest_scan: null | { id: number; status: string };
}

export interface StorageArea {
  bucket: string;
  purpose_codes: Array<
    | 'source_dwg'
    | 'derived_dwg'
    | 'reports'
    | 'temporary'
    | 'source_dxf'
    | 'derived_dxf'
  >;
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
  original_name: string;
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
