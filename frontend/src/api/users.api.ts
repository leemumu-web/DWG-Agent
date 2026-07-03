import { apiClient, type PageEnvelope } from './client';
import type { User } from '../types/user';

export async function listUsers() {
  const res = await apiClient.get<PageEnvelope<User>>('/api/v1/users');
  return res.data.data;
}
