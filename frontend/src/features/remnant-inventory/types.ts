export type RemnantStatus = 'available' | 'reserved' | 'used' | 'archived';

export interface RemnantMaterial {
  id: number;
  code: string;
  family_code: string;
  enabled: boolean;
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

export interface OriginalDownload {
  file_id: number;
  file_name: string;
  file_ext: '.dwg' | '.dxf';
  url: string;
  expires_in: number;
}

