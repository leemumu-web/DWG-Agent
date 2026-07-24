export interface Project {
  id: number;
  code: string;
  name: string;
  description?: string | null;
  owner_id?: number | null;
  owner_name?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}
