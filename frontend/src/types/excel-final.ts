export interface ExcelFinalBatch {
  batch_id: number;
  job_id: number;
  file_id: number | null;
  source_type: string;
  source_name: string | null;
  part_count: number;
  component_count: number;
  total_net_weight: number | null;
  total_gross_weight: number | null;
  created_at: string | null;
}

export interface ExcelFinalPart {
  id: number;
  batch_id?: number;
  seq: number;
  component_no: string | null;
  component_qty: number | null;
  part_type: string | null;
  part_no: string | null;
  profile_spec: string | null;
  spec: string | null;
  width: number | null;
  length: number | null;
  left_inset?: number | null;
  right_inset?: number | null;
  cut_length: number | null;
  material: string | null;
  qty: number | null;
  total_qty: number | null;
  total_length: number | null;
  density: number | null;
  theo_unit_weight: number | null;
  theo_total_weight: number | null;
  net_unit_weight: number | null;
  net_total_weight: number | null;
  table_net_weight: number | null;
  gross_unit_weight: number | null;
  gross_total_weight: number | null;
  table_gross_weight: number | null;
  surface_area: number | null;
  total_surface_area: number | null;
  created_at?: string | null;
}

export interface ExcelFinalComponent {
  id: number;
  component_no: string | null;
  component_qty: number | null;
  total_weight: number | null;
}

export interface ExcelFinalStatus {
  job_id: number;
  status: string;
  progress: number;
  pipeline: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  batch: {
    batch_id: number;
    source_type: string;
    source_name: string | null;
    part_count: number;
    component_count: number;
    total_net_weight: number | null;
    total_gross_weight: number | null;
  } | null;
  result_file_id: number | null;
}

export interface BatchDetail extends ExcelFinalBatch {
  material_breakdown: { material: string; count: number; total_net_weight: number | null }[];
  top_specs: { spec: string; count: number }[];
}

export interface WeightLookupResult {
  spec: string;
  weight_kg_per_m: number;
  source: string;
}

export interface UploadResult {
  file_id: number;
  original_name: string;
  file_ext: string;
  size_bytes: number;
  bucket: string;
}

export interface ProcessResult {
  job_id: number;
  file_id: number;
  status: string;
  message: string;
}

export interface UploadAndProcessResult extends ProcessResult {
  original_name: string;
}

export interface ExcelFinalHealth {
  pipeline_enabled: boolean;
  package_available: boolean;
  handbook_available: boolean;
  ready: boolean;
}
