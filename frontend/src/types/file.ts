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
  status: string;
  created_at: string;
  updated_at: string;
}
