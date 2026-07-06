import { apiClient, fetchAllPages, type ApiEnvelope, type PageEnvelope } from './client';
import type { Project } from '../types/project';

export interface ProjectMember {
  id: number;
  project_id: number;
  user_id: number;
  project_role: string;
  created_at: string;
}

export async function listProjects() {
  return fetchAllPages<Project>('/api/v1/projects');
}

export async function getProject(projectId: number) {
  const res = await apiClient.get<ApiEnvelope<Project>>(`/api/v1/projects/${projectId}`);
  return res.data.data;
}

export async function createProject(payload: { code: string; name: string; description?: string }) {
  const res = await apiClient.post<ApiEnvelope<Project>>('/api/v1/projects', payload);
  return res.data.data;
}

export async function updateProject(
  projectId: number,
  payload: { name?: string; description?: string; status?: string },
) {
  const res = await apiClient.patch<ApiEnvelope<Project>>(`/api/v1/projects/${projectId}`, payload);
  return res.data.data;
}

export async function deleteProject(projectId: number) {
  await apiClient.delete(`/api/v1/projects/${projectId}`);
}

export async function listProjectMembers(projectId: number) {
  const res = await apiClient.get<PageEnvelope<ProjectMember>>(
    `/api/v1/projects/${projectId}/members`,
    { params: { page_size: 200 } },
  );
  return res.data.data;
}

export async function addProjectMember(
  projectId: number,
  payload: { user_id: number; project_role: string },
) {
  const res = await apiClient.post<ApiEnvelope<ProjectMember>>(
    `/api/v1/projects/${projectId}/members`,
    payload,
  );
  return res.data.data;
}

export async function updateProjectMember(
  projectId: number,
  memberId: number,
  project_role: string,
) {
  const res = await apiClient.patch<ApiEnvelope<ProjectMember>>(
    `/api/v1/projects/${projectId}/members/${memberId}`,
    { project_role },
  );
  return res.data.data;
}

export async function removeProjectMember(projectId: number, memberId: number) {
  await apiClient.delete(`/api/v1/projects/${projectId}/members/${memberId}`);
}
