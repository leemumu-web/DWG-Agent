export interface AnalysisResult {
  id: number;
  job_id: number;
  drawing_id?: number | null;
  result_type: string;
  result_json?: Record<string, unknown> | null;
  confidence?: string | null;
  result_file_id?: number | null;
  status: string;
  created_at: string;
  updated_at: string;
}
