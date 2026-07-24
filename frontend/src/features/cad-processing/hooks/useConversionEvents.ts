import { useEffect, useRef } from 'react';
import type { Job } from '../../jobs';

const VITE_API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';
const MAX_STREAM_FILES = 200;
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);

interface ConversionSnapshot {
  type: 'snapshot' | 'update';
  jobs: Array<Partial<Job> & { job_id: number }>;
}

function chunksOf<T>(values: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let index = 0; index < values.length; index += size) {
    chunks.push(values.slice(index, index + size));
  }
  return chunks;
}

/** Subscribe to bounded aggregate conversion streams and merge durable job snapshots. */
export function useConversionEvents(
  taskType: string,
  fileIds: number[],
  onJobs: (jobs: Array<Partial<Job> & { id: number }>) => void,
) {
  const callbackRef = useRef(onJobs);
  callbackRef.current = onJobs;
  const stableIds = Array.from(new Set(fileIds)).sort((a, b) => a - b).join(',');

  useEffect(() => {
    if (!stableIds) return;
    const streams: EventSource[] = [];
    const ids = stableIds.split(',').map(Number);

    for (const chunk of chunksOf(ids, MAX_STREAM_FILES)) {
      const params = new URLSearchParams({ task_type: taskType, file_ids: chunk.join(',') });
      const stream = new EventSource(
        `${VITE_API_BASE_URL}/api/v1/workflows/jobs/events/stream?${params.toString()}`,
        { withCredentials: true },
      );
      streams.push(stream);
      stream.onmessage = (message) => {
        try {
          const payload = JSON.parse(message.data) as ConversionSnapshot;
          const patches = payload.jobs.map((job) => {
            const { job_id: id, ...rest } = job;
            return { ...rest, id };
          });
          callbackRef.current(patches);
          if (payload.jobs.length > 0 && payload.jobs.every((job) => TERMINAL.has(job.status ?? ''))) {
            stream.close();
          }
        } catch {
          // Keep the stream alive when a malformed frame is encountered.
        }
      };
    }

    return () => streams.forEach((stream) => stream.close());
  }, [stableIds, taskType]);
}
