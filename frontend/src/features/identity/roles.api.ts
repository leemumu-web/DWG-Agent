import { apiClient, fetchAllPages } from '../../shared/api';
import type { Permission, Role } from '../../shared/auth';

export async function listRoles() {
  return fetchAllPages<Role>('/api/v1/roles');
}

export async function listPermissions() {
  return fetchAllPages<Permission>('/api/v1/permissions');
}

export async function replaceRolePermissions(roleId: number, permission_codes: string[]) {
  const res = await apiClient.put(`/api/v1/roles/${roleId}/permissions`, { permission_codes });
  return res.data.data;
}
