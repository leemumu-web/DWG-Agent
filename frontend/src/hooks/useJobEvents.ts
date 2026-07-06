import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../stores/auth.store';
import type { Job } from '../types/job';

const VITE_API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

interface JobEvent {
  type: string;
  job_id: number;
  status?: string;
  progress?: number;
  step_name?: string;
  message?: string;
  error_code?: string;
  pipeline?: string;
  task_type?: string;
}

/**
 * Subscribe to job progress via SSE (Server-Sent Events).
 *
 * When a progress event arrives, the TanStack Query cache for ['jobs'] is
 * optimistically updated so every component showing job data re-renders
 * immediately — no polling needed.
 *
 * The connection auto-closes when the job reaches a terminal state
 * (done / error / succeeded / failed / cancelled), or when the component
 * unmounts, or when jobId changes.
 */
export function useJobEvents(jobId: number | null) {
  const queryClient = useQueryClient();
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (jobId === null || jobId === undefined) {
      return;
    }

    const token = useAuthStore.getState().accessToken;
    if (!token) return;

    const url = `${VITE_API_BASE_URL}/api/v1/jobs/${jobId}/events?token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (msg) => {
      try {
        const event: JobEvent = JSON.parse(msg.data);

        // Optimistically update the job in the jobs list cache
        queryClient.setQueryData<Job[]>(['jobs'], (old) => {
          if (!old) return old;
          return old.map((j) => {
            if (j.id !== event.job_id) return j;
            return {
              ...j,
              status: event.status ?? j.status,
              progress: event.progress ?? j.progress,
              pipeline: event.pipeline ?? j.pipeline,
              error_code: event.error_code ?? j.error_code,
              error_message: event.message ?? j.error_message,
            } as Job;
          });
        });

        // On terminal event, close the stream
        if (
          event.type === 'done' ||
          event.type === 'error' ||
          event.status === 'succeeded' ||
          event.status === 'failed' ||
          event.status === 'cancelled'
        ) {
          es.close();
        }
      } catch {
        // Ignore parse errors (keepalive comments, etc.)
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects; if we get a persistent error (e.g. 404),
      // close it after a brief delay to avoid tight reconnect loops.
      es.close();
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [jobId, queryClient]);
}
