import { apiClient, type ApiEnvelope } from './client';
import type { LoginResponse } from '../types/auth';
import type { User } from '../types/user';

export async function login(username: string, password: string) {
  const res = await apiClient.post<ApiEnvelope<LoginResponse>>('/api/v1/auth/sessions', { username, password });
  return res.data.data;
}

export async function getMe() {
  const res = await apiClient.get<ApiEnvelope<User>>('/api/v1/auth/me');
  return res.data.data;
}
