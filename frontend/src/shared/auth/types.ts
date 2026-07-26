export interface Role {
  id: number;
  code: string;
  name: string;
  description?: string | null;
  is_system: boolean;
  permissions?: Permission[];
}

export interface Permission {
  id: number;
  code: string;
  resource: string;
  action: string;
  name: string;
}

export interface User {
  id: number;
  username: string;
  employee_no?: string | null;
  real_name: string;
  email?: string | null;
  status: string;
  password_reset_required: boolean;
  roles: Role[];
  created_at: string;
  updated_at: string;
}
