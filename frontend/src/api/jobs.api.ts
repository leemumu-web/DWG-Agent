import { apiClient, type ApiEnvelope, type PageEnvelope } from './client';
import type { Job, JobStep } from '../types/job';

export interface JobResult {
  id: number;
  job_id: number;
  drawing_id?: number | null;
  result_type: string;
  result_json?: Record<string, unknown> | null;
  confidence: number;
  result_file_id?: number | null;
  algorithm_version?: string | null;
  tool_version?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export async function listJobs() {
  const res = await apiClient.get<PageEnvelope<Job>>('/api/v1/jobs');
  return res.data.data;
}

export async function getJob(jobId: number) {
  const res = await apiClient.get<ApiEnvelope<Job>>(`/api/v1/jobs/${jobId}`);
  return res.data.data;
}

export async function getJobSteps(jobId: number) {
  const res = await apiClient.get<PageEnvelope<JobStep>>(`/api/v1/jobs/${jobId}/steps`);
  return res.data.data;
}

export async function getJobResults(jobId: number) {
  const res = await apiClient.get<PageEnvelope<JobResult>>(`/api/v1/jobs/${jobId}/results`);
  return res.data.data;
}

export async function retryJob(jobId: number) {
  const res = await apiClient.post<ApiEnvelope<Job>>(`/api/v1/jobs/${jobId}/retry-requests`);
  return res.data.data;
}

export async function createDxfJob(fileId: number) {
  const res = await apiClient.post<ApiEnvelope<Job>>('/api/v1/jobs', {
    task_type: 'convert_dwg_to_dxf',
    precision_level: 'normal',
    params: { file_id: fileId },
  });
  return res.data.data;
}

export async function createFrameworkSmokeJob() {
  const res = await apiClient.post<ApiEnvelope<Job>>('/api/v1/jobs', {
    task_type: 'framework_smoke_test',
    precision_level: 'normal',
    params: { source: 'frontend' },
  });
  return res.data.data;
}
