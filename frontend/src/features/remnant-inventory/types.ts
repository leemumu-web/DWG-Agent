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

export interface RemnantImportItem {
  id: number;
  batch_id: number;
  source_file_id: number;
  dxf_file_id: number | null;
  original_name: string;
  source_ext: '.dwg' | '.dxf';
  attempt: number;
  status: RemnantImportItemStatus;
  material_candidates: RemnantCandidate[];
  project_candidates: RemnantCandidate[];
  part_candidates: RemnantCandidate[];
  warnings: { code: string; message: string }[];
  thickness_mm: string | null;
  material_id: number | null;
  project_no: string | null;
  parts: string[];
  error_code: string | null;
  error_message: string | null;
}

export interface RemnantImportBatch {
  id: number;
  created_by: number;
  status: 'uploaded' | 'processing' | 'awaiting_confirmation' | 'confirmed' | 'cancelled';
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
