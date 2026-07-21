export interface Job {
  id: number;
  project_id?: number | null;
  drawing_id?: number | null;
  created_by?: number | null;
  task_type: string;
  precision_level: string;
  pipeline?: string | null;
  status: string;
  attempt: number;
  priority: number;
  progress: number;
  params_json?: Record<string, unknown> | null;
  error_code?: string | null;
  error_message?: string | null;
  progress_data?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface JobStep {
  id: number;
  job_id: number;
  attempt: number;
  step_name: string;
  worker_name?: string | null;
  status: string;
  input_json?: Record<string, unknown> | null;
  output_json?: Record<string, unknown> | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}
