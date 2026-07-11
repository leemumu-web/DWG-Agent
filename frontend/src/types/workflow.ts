export interface WorkflowStage {
  id: number;
  stage_code: string;
  name: string;
  sequence: number;
  status: string;
  job_id?: number | null;
  job_attempt?: number | null;
  progress: number;
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowArtifact {
  id: number;
  stage_run_id?: number | null;
  artifact_type: string;
  file_id?: number | null;
  result_id?: number | null;
  version: number;
  metadata_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowRun {
  id: number;
  project_id: number;
  created_by: number;
  name: string;
  workflow_type: string;
  status: string;
  current_stage?: string | null;
  progress: number;
  config_json?: Record<string, unknown> | null;
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowDetail extends WorkflowRun {
  stages: WorkflowStage[];
  artifacts: WorkflowArtifact[];
}
