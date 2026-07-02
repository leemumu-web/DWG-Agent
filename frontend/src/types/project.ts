export interface Project {
  id: number;
  code: string;
  name: string;
  description?: string | null;
  owner_id?: number | null;
  status: string;
  created_at: string;
  updated_at: string;
}
