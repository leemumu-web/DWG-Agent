import { apiClient, fetchAllPages, type ApiEnvelope } from '../../shared/api';
import type { User } from '../../shared/auth';

export async function listUsers() {
  return fetchAllPages<User>('/api/v1/users');
}

/** Self-service profile update — any authenticated user may update their own
 *  real_name / email via PATCH /users/me. */
export async function updateSelf(payload: { real_name?: string; email?: string }) {
  const res = await apiClient.patch<ApiEnvelope<User>>('/api/v1/users/me', payload);
  return res.data.data;
}

export async function createUser(payload: {
  username: string;
  password: string;
  real_name: string;
  employee_no?: string;
  email?: string;
}) {
  const res = await apiClient.post<ApiEnvelope<User>>('/api/v1/users', payload);
  return res.data.data;
}

export async function updateUser(
  userId: number,
  payload: { real_name?: string; email?: string; status?: string },
) {
  const res = await apiClient.patch<ApiEnvelope<User>>(`/api/v1/users/${userId}`, payload);
  return res.data.data;
}

export async function deleteUser(userId: number) {
  await apiClient.delete(`/api/v1/users/${userId}`);
}

export async function assignRole(userId: number, role_code: string) {
  const res = await apiClient.post<ApiEnvelope<User>>(`/api/v1/users/${userId}/roles`, {
    role_code,
  });
  return res.data.data;
}

export async function removeRole(userId: number, roleId: number) {
  await apiClient.delete(`/api/v1/users/${userId}/roles/${roleId}`);
}

export async function resetUserPassword(userId: number) {
  const res = await apiClient.post<
    ApiEnvelope<{ user_id: number; temp_password: string; message: string }>
  >(`/api/v1/users/${userId}/password-reset-requests`);
  return res.data.data;
}

export async function disableUser(userId: number) {
  const res = await apiClient.post<ApiEnvelope<User>>(`/api/v1/users/${userId}/disable-requests`);
  return res.data.data;
}

export async function enableUser(userId: number) {
  const res = await apiClient.post<ApiEnvelope<User>>(`/api/v1/users/${userId}/enable-requests`);
  return res.data.data;
}
