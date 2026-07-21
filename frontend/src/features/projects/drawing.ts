export interface Drawing {
  id: number;
  project_id: number;
  drawing_no?: string | null;
  title?: string | null;
  discipline?: string | null;
  current_version_id?: number | null;
  status: string;
  created_at: string;
  updated_at: string;
}
