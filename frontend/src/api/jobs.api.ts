import { apiClient, fetchAllPages, type ApiEnvelope, type PageEnvelope } from './client';
import type { Job, JobStep } from '../types/job';
import type { AnalysisResult } from '../types/result';

/** Re-export so existing `import type { JobResult }` keeps working. The
 *  canonical type lives in types/result.ts (AnalysisResult) — job results
 *  and pending reviews are the same backend AnalysisResultRead shape. */
export type JobResult = AnalysisResult;

/** Fetch ALL jobs across pages, optionally filtered by task_type.
 *  The FilesPage in-memory files↔jobs join via params.file_id needs every
 *  job for the given task type to be present. Uses page_size=200
 *  (the backend hard maximum) and aggregates pages until total is reached. */
export async function listJobs(taskType?: string) {
  const params: Record<string, unknown> = {};
  if (taskType) params.task_type = taskType;
  return fetchAllPages<Job>('/api/v1/jobs', params);
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
  const res = await apiClient.get<PageEnvelope<JobResult>>(`/api/v1/jobs/${jobId}/results`, {
    params: { page_size: 200 },
  });
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

export async function createDxf2DwgJob(fileId: number) {
  const res = await apiClient.post<ApiEnvelope<Job>>('/api/v1/jobs', {
    task_type: 'convert_dxf_to_dwg',
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

export async function cancelAllJobs(): Promise<{ cancelled_count: number }> {
  const res = await apiClient.post<ApiEnvelope<{ cancelled_count: number }>>(
    '/api/v1/jobs/cancel-all-active',
  );
  return res.data.data;
}
