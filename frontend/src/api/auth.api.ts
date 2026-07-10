import { apiClient, type ApiEnvelope } from './client';
import type { LoginResponse } from '../types/auth';
import type { User } from '../types/user';

export async function login(username: string, password: string) {
  const res = await apiClient.post<ApiEnvelope<LoginResponse>>('/api/v1/auth/sessions', {
    username,
    password,
  });
  return res.data.data;
}

export async function logout() {
  await apiClient.delete('/api/v1/auth/sessions/current');
}

/** Refresh the access token using the HttpOnly refresh cookie. The backend
 *  reads the cookie automatically — no body needed. Returns a fresh
 *  access_token + user. Missing or stale cookies reject with 401. */
export async function refreshSession(): Promise<LoginResponse> {
  const res = await apiClient.post<ApiEnvelope<LoginResponse>>('/api/v1/auth/tokens/refresh');
  return res.data.data;
}

export async function getMe() {
  const res = await apiClient.get<ApiEnvelope<User>>('/api/v1/auth/me');
  return res.data.data;
}

export async function changePassword(current_password: string, new_password: string) {
  const res = await apiClient.patch<ApiEnvelope<{ changed: boolean }>>(
    '/api/v1/auth/password',
    { current_password, new_password },
  );
  return res.data.data;
}
