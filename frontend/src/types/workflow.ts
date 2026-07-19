export interface WorkflowStage {
  id: number;
  stage_code: string;
  name: string;
  sequence: number;
  status: string;
  job_id?: number | null;
  job_attempt?: number | null;
  progress: number;
  input_json?: Record<string, unknown> | null;
  output_json?: Record<string, unknown> | null;
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

export interface WorkflowStageCapability {
  code: string;
  name: string;
  description: string;
  execution_mode: 'manual' | 'automated' | 'placeholder' | 'external';
  implementation_status: 'implemented' | 'placeholder' | 'external';
  execution_kind?: string | null;
  required_inputs: string[];
  artifact_types: string[];
}

export interface WorkflowTemplate {
  code: 'linux_production' | 'excel_delivery' | 'file_delivery';
  name: string;
  description: string;
  stages: WorkflowStageCapability[];
}

export interface WorkflowArtifactCreatePayload {
  stage_code: string;
  artifact_type: string;
  file_id?: number;
  result_id?: number;
  metadata?: Record<string, unknown>;
}

export interface WorkflowStageExecutionPayload {
  execution_kind: string;
  batch_name?: string;
  file_id?: number;
}
