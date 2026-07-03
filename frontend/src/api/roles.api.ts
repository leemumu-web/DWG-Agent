import { apiClient, type PageEnvelope } from './client';
import type { Permission, Role } from '../types/user';

export async function listRoles() {
  const res = await apiClient.get<PageEnvelope<Role>>('/api/v1/roles');
  return res.data.data;
}

export async function listPermissions() {
  const res = await apiClient.get<PageEnvelope<Permission>>('/api/v1/permissions');
  return res.data.data;
}
