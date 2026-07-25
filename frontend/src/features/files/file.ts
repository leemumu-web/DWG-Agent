export interface StoredFile {
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

export interface BatchInfo {
  name: string;
  file_count: number;
  latest_created_at: string;
}

export interface DxfPreviewBounds {
  min_x: number;
  min_y: number;
  max_x: number;
  max_y: number;
}

export interface DxfPreviewResponse {
  file_id: number;
  file_name: string;
  preview_file_id: number;
  content_url: string;
  content_type: 'image/svg+xml';
  document_entities: number;
  modelspace_entities: number;
  entity_counts: Record<string, number>;
  layers: string[];
  layer_colors: Record<string, number>;
  bounds: DxfPreviewBounds;
  cached: boolean;
}

export interface ExcelPreviewResponse {
  file: string;
  file_id: number;
  sheets: string[];
  sheet: string;
  headers: string[];
  rows: Record<string, unknown>[];
  total_rows: number;
}
