import { apiClient, type ApiEnvelope } from '../../shared/api';
import type { Project } from './project';

/** List active projects for workflow/dashboard filter dropdowns.
 *
 *  The full /projects CRUD endpoint has been removed; this now calls
 *  GET /workflows/projects — a minimal read-only projection served by
 *  the workflows subsystem.
 */
export async function listProjects() {
  const res = await apiClient.get<ApiEnvelope<Project[]>>('/api/v1/workflows/projects');
  return res.data.data;
}
