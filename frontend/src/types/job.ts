export interface Job {
  id: number;
  project_id?: number | null;
  drawing_id?: number | null;
  created_by?: number | null;
  task_type: string;
  precision_level: string;
  pipeline?: string | null;
  status: string;
  priority: number;
  progress: number;
  created_at: string;
  updated_at: string;
}
