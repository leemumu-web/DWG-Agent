import { apiClient, type PageEnvelope } from './client';
import type { Project } from '../types/project';

export async function listProjects() {
  const res = await apiClient.get<PageEnvelope<Project>>('/api/v1/projects');
  return res.data.data;
}
