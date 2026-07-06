export interface AnalysisResult {
  id: number;
  job_id: number;
  drawing_id?: number | null;
  result_type: string;
  result_json?: Record<string, unknown> | null;
  /** Confidence in [0, 1] — backend stores Decimal, serialized as a JSON number. */
  confidence?: number | null;
  result_file_id?: number | null;
  algorithm_version?: string | null;
  tool_version?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}
