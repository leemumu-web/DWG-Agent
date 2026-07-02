import { apiClient, type PageEnvelope } from './client';
import type { Job } from '../types/job';

export async function listJobs() {
  const res = await apiClient.get<PageEnvelope<Job>>('/api/v1/jobs');
  return res.data.data;
}

export async function createFrameworkSmokeJob() {
  const res = await apiClient.post('/api/v1/jobs', {
    task_type: 'framework_smoke_test',
    precision_level: 'normal',
    params: { source: 'frontend' },
  });
  return res.data.data as Job;
}
