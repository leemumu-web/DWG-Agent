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
  created_at: string;
  updated_at: string;
}

export interface BatchInfo {
  name: string;
  file_count: number;
  latest_created_at: string;
}

export interface DxfEntityData {
  x1?: number; y1?: number; x2?: number; y2?: number;
  cx?: number; cy?: number; r?: number;
  start_angle?: number; end_angle?: number;
  x?: number; y?: number; text?: string; height?: number; rotation?: number;
  points?: number[][]; closed?: boolean;
  control_points?: number[][]; degree?: number;
  major?: number[]; ratio?: number; start?: number; end?: number;
  name?: string; scale_x?: number; scale_y?: number;
}

export interface DxfEntity {
  type: string;
  layer: string;
  color: number;
  data: DxfEntityData;
}

export interface DxfBounds {
  min_x: number; min_y: number;
  max_x: number; max_y: number;
}

export interface DxfPreviewResponse {
  file_id: number;
  file_name: string;
  preview_url: string;
  entity_counts: Record<string, number>;
  total_entities: number;
  layers: string[];
  layer_colors: Record<string, number>;
  bounds: DxfBounds;
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
