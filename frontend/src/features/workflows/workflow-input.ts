import type { StoredFile } from '../files';
import type { Job } from '../jobs';

export interface WorkflowInputCounts {
  dwg: number;
  excel: number;
  paired: number;
  converting: number;
  failed: number;
}

export interface WorkflowInputIssue {
  item_id?: number | null;
  file_name?: string | null;
  code: string;
  message: string;
  recommended_action: string;
}

export interface WorkflowInputItem {
  id: number;
  role: 'source_dwg' | 'source_excel';
  status: string;
  original_name: string;
  normalized_stem: string;
  file: StoredFile;
  conversion_job?: Job | null;
  derived_dxf?: StoredFile | null;
  drawing_id?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  validation?: Record<string, unknown> | null;
}

export interface WorkflowInputBatch {
  id: number;
  workflow_run_id: number;
  project_id: number;
  status: string;
  version: number;
  manifest_sha256?: string | null;
  frozen_at?: string | null;
  counts: WorkflowInputCounts;
  items: WorkflowInputItem[];
  issues: WorkflowInputIssue[];
  freeze_ready: boolean;
  recoverable_file_count: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowInputConversion {
  batch: WorkflowInputBatch;
  jobs: Job[];
  dispatched_count: number;
}
