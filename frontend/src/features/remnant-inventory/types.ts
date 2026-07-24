export type RemnantStatus = 'available' | 'reserved' | 'used' | 'archived';

export interface RemnantMaterial {
  id: number;
  code: string;
  family_code: string;
  enabled: boolean;
  aliases: string[];
  created_at: string;
  updated_at: string;
}

export interface Remnant {
  id: number;
  source_file_id: number;
  dxf_file_id: number;
  source_name: string;
  source_ext: '.dwg' | '.dxf';
  thickness_mm: string;
  material_id: number;
  material_code: string;
  project_no: string;
  project_no_secondary: string | null;
  storage_location: string | null;
  remark_1: string | null;
  remark_2: string | null;
  parts: string[];
  status: RemnantStatus;
  imported_by: number;
  reserved_by: number | null;
  reserved_by_name: string | null;
  reserved_at: string | null;
  used_by: number | null;
  used_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface RemnantSearch {
  materialId?: number;
  thicknessMm?: string;
  includeFamily: boolean;
  statuses: RemnantStatus[];
  page: number;
}

export type RemnantGlobalSort = 'created_desc' | 'created_asc' | 'thickness_asc' | 'thickness_desc' | 'status';

export interface RemnantGlobalSearch {
  materialId?: number;
  thicknessMm?: string;
  statuses: RemnantStatus[];
  project?: string;
  projectSecondary?: string;
  storageLocation?: string;
  remark1?: string;
  remark2?: string;
  part?: string;
  sort: RemnantGlobalSort;
  page: number;
}

export interface BulkArchiveResult {
  archived: number[];
  failed: Array<{
    remnant_id: number;
    code: string;
    message: string;
  }>;
}

export interface OriginalDownload {
  file_id: number;
  file_name: string;
  file_ext: '.dwg' | '.dxf';
  url: string;
  expires_in: number;
}

export type RemnantImportItemStatus = 'uploaded' | 'converting' | 'parsing'
  | 'pending_confirmation' | 'confirmed' | 'failed' | 'cancelled';

export interface CandidateEvidence {
  raw_text: string;
  entity_type: string;
  layer: string;
  block_path: string[];
  x?: number;
  y?: number;
}

export interface RemnantCandidate {
  value: string;
  evidence: CandidateEvidence[];
}

export interface RemnantStandardParse {
  block_type: string;
  raw_specification: string;
  thickness: string;
  length: string;
  width: string;
  material: string;
  remnant_number: string;
}

export interface RemnantImportItem {
  id: number;
  batch_id: number;
  source_file_id: number;
  dxf_file_id: number | null;
  original_name: string;
  source_ext: '.dwg' | '.dxf';
  source_relative_path?: string | null;
  attempt: number;
  status: RemnantImportItemStatus;
  material_candidates: RemnantCandidate[];
  project_candidates: RemnantCandidate[];
  part_candidates: RemnantCandidate[];
  warnings: { code: string; message: string }[];
  standard_parse?: RemnantStandardParse | null;
  thickness_mm: string | null;
  material_id: number | null;
  project_no: string | null;
  project_no_secondary: string | null;
  storage_location: string | null;
  remark_1: string | null;
  remark_2: string | null;
  parts: string[];
  error_code: string | null;
  error_message: string | null;
}

export interface RemnantImportBatch {
  id: number;
  created_by: number;
  import_mode?: 'manual' | 'auto';
  default_project_no?: string | null;
  source_folder_name?: string | null;
  status: 'uploaded' | 'processing' | 'awaiting_confirmation' | 'confirmed' | 'failed' | 'cancelled';
  total_count: number;
  converting_count: number;
  parsing_count: number;
  pending_count: number;
  confirmed_count: number;
  failed_count: number;
  cancelled_count: number;
  items: RemnantImportItem[];
  created_at: string;
  updated_at: string;
}

export interface ImportConfirmationResult {
  confirmed: { item_id: number; remnant_id: number }[];
  invalid: { item_id: number; code: string }[];
  already_confirmed: { item_id: number; remnant_id: number }[];
}
