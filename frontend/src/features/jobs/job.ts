// Job 跨模块契约（files/cad-processing/workflows/excel-processing 共用）：
// attempt 是执行代次——旧 attempt 不覆盖新状态；result_available=false
// 表示结果已被释放、文件可重新提交；status 的 terminal 集合为
// succeeded/failed/cancelled。
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
  source_name?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  progress_data?: Record<string, unknown> | null;
  result_available?: boolean | null;
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
