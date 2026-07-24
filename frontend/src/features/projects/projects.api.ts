import { apiClient, type ApiEnvelope } from '../../shared/api';
import type { Project } from './project';

/** List accessible active projects from the workflow-scoped project resource. */
export async function listProjects() {
  const res = await apiClient.get<ApiEnvelope<Project[]>>('/api/v1/workflows/projects');
  return res.data.data;
}
