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

export interface ExcelPreviewResponse {
  file: string;
  file_id: number;
  sheets: string[];
  sheet: string;
  headers: string[];
  rows: Record<string, unknown>[];
  total_rows: number;
}
