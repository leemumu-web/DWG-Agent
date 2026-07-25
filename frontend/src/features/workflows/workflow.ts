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
  project_code?: string | null;
  project_name?: string | null;
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
  required_outputs: string[];
}

export interface WorkflowTemplate {
  code: 'linux_production' | 'excel_delivery' | 'file_delivery';
  name: string;
  description: string;
  stages: WorkflowStageCapability[];
}

export interface WorkflowStageExecutionPayload {
  execution_kind: string;
}

export interface DxfClassificationItem {
  id: number;
  drawing_id?: number | null;
  source_file: import('../files').StoredFile;
  output_file: import('../files').StoredFile;
  source_name: string;
  output_name: string;
  output_directory: string;
  disposition: 'classified' | 'review_required' | 'unreadable';
  part_type?: string | null;
  diagnostics: string[];
}

export type DxfClassificationTypeSource =
  | 'catalog'
  | 'auto_discovered'
  | 'legacy';

export interface DxfClassificationGroup {
  group_key: string;
  label: string;
  part_type?: string | null;
  type_source?: DxfClassificationTypeSource | null;
  disposition: 'classified' | 'review_required' | 'unreadable';
  count: number;
  warning_count: number;
  total_size_bytes: number;
}

export interface DxfClassificationGroupItem {
  output_name: string;
  part_type?: string | null;
  profile_raw?: string | null;
  profile_normalized?: string | null;
  type_source?: DxfClassificationTypeSource | null;
  disposition: 'classified' | 'review_required' | 'unreadable';
  diagnostics: string[];
  size_bytes: number;
}

export interface DxfClassificationGroupPage {
  items: DxfClassificationGroupItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface DxfClassificationRun {
  id: number;
  workflow_run_id: number;
  status: 'running' | 'completed' | 'completed_with_review' | 'failed';
  classifier_version: string;
  report_schema?: string | null;
  cli_schema?: string | null;
  project_name: string;
  input_manifest_sha256: string;
  input_count: number;
  classified_count: number;
  review_required_count: number;
  unreadable_count: number;
  type_counts: Record<string, number>;
  groups: DxfClassificationGroup[];
  report_file?: import('../files').StoredFile | null;
  manifest_file?: import('../files').StoredFile | null;
  job: import('../jobs').Job;
  items: DxfClassificationItem[];
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DxfSplitItem {
  id: number;
  drawing_id?: number | null;
  classification_item_id: number;
  source_file_id: number;
  source_name: string;
  part_type: string;
  profile_normalized?: string | null;
  family?: string | null;
  source_contract_id?: string | null;
  automation_route: 'auto_accepted' | 'manual_review';
  disposition: string;
  normal_dxf_file_id?: number | null;
  weld_allowance_dxf_file_id?: number | null;
  split_report_file_id?: number | null;
  weld_allowance_report_file_id?: number | null;
  diagnostics: string[];
  validation: Record<string, unknown>;
}

export interface DxfSplitRun {
  id: number;
  workflow_run_id: number;
  status: 'running' | 'completed' | 'completed_with_review' | 'failed';
  splitter_version: string;
  cli_schema?: string | null;
  validation_schema?: string | null;
  input_manifest_sha256: string;
  input_count: number;
  auto_accepted_count: number;
  manual_review_count: number;
  source_contracts: Record<string, string>;
  bh_split_ledger_file?: import('../files').StoredFile | null;
  split_manifest_file?: import('../files').StoredFile | null;
  validation_report_file?: import('../files').StoredFile | null;
  job: import('../jobs').Job;
  items: DxfSplitItem[];
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
}
