import { apiClient, type ApiEnvelope, type PageEnvelope } from './client';
import type { Job, JobStep } from '../types/job';

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
