import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Job, JobStep } from '../types/job';

const VITE_API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

interface JobEvent {
  type: string;
  job_id: number;
  attempt?: number;
  status?: string;
  progress?: number;
  step_name?: string;
  message?: string;
  error_code?: string;
  error_message?: string;
  pipeline?: string;
  task_type?: string;
  /** snapshot / terminal frames carry a steps[] array; progress frames do not. */
  steps?: { attempt: number; step_name: string; status: string; error_message?: string | null }[];
}

interface JobEventUpdate {
  /** Merged into the matching Job in the ['jobs'] query cache. */
  jobPatch: Partial<Job> & { id: number };
  /** Present only on snapshot/terminal frames that carry step data. */
  steps?: JobStep[];
}

/**
 * Subscribe to job progress via SSE (Server-Sent Events).
 *
 * When a progress event arrives, the TanStack Query cache for ['jobs'] is
 * optimistically updated so every component showing job data re-renders
 * immediately — no polling needed. An optional `onEvent` callback receives
 * the parsed update (useful for a detail drawer that holds local step state).
 *
 * The connection auto-closes when the job reaches a terminal state
 * (done / error / succeeded / failed / cancelled), or when the component
 * unmounts, or when jobId changes.
 */
export function useJobEvents(
  jobId: number | null,
  onEvent?: (update: JobEventUpdate) => void,
) {
  const queryClient = useQueryClient();
  const esRef = useRef<EventSource | null>(null);
  // Keep the latest callback in a ref so the SSE listener added once per jobId
  // always calls the freshest closure (avoids re-subscribing on every render).
  const cbRef = useRef(onEvent);
  cbRef.current = onEvent;
  // Once we've seen a terminal frame, stop reconnecting — the job is finished.
  const terminalRef = useRef(false);

  useEffect(() => {
    if (jobId === null || jobId === undefined) {
      return;
    }

    // Fresh job → not terminal yet. (terminalRef persists across renders, so
    // reset it per job to avoid the previous job's terminal state leaking in.)
    terminalRef.current = false;

    // EventSource auto-reconnects on transient errors (Nginx proxy_read_timeout
    // cut, network blip, server restart). We only close manually for terminal
    // job states or hard auth errors (401/403/404). Without this distinction,
    // a single 1h Nginx cut would orphan the stream for a still-running job.
    const close = () => {
      esRef.current?.close();
      esRef.current = null;
    };

    const url = `${VITE_API_BASE_URL}/api/v1/jobs/${jobId}/events`;
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    const apply = (event: JobEvent) => {
      const jobPatch: Partial<Job> & { id: number } = {
        id: event.job_id,
        attempt: event.attempt,
        status: event.status,
        progress: event.progress,
        pipeline: event.pipeline,
        error_code: event.error_code,
        error_message: event.error_message ?? event.message,
      };
      // Strip undefined keys so we don't blow away existing cache values.
      for (const k of Object.keys(jobPatch) as (keyof typeof jobPatch)[]) {
        if (jobPatch[k] === undefined) delete jobPatch[k];
      }

      queryClient.setQueryData<Job[]>(['jobs'], (old) => {
        if (!old) return old;
        return old.map((j) => (j.id === event.job_id ? { ...j, ...jobPatch } : j));
      });

      const steps: JobStep[] | undefined = event.steps?.map((s, i) => ({
        id: i,
        job_id: event.job_id,
        attempt: s.attempt,
        step_name: s.step_name,
        status: s.status,
        error_message: s.error_message ?? null,
        worker_name: null,
        input_json: null,
        output_json: null,
        started_at: null,
        finished_at: null,
      }));

      cbRef.current?.({ jobPatch, steps });
    };

    es.onmessage = (msg) => {
      try {
        const event: JobEvent = JSON.parse(msg.data);
        apply(event);

        // On terminal event, close the stream and stop reconnecting.
        if (
          event.type === 'done' ||
          event.type === 'error' ||
          event.status === 'succeeded' ||
          event.status === 'failed' ||
          event.status === 'cancelled'
        ) {
          terminalRef.current = true;
          close();
        }
      } catch {
        // Ignore parse errors (keepalive comments, etc.)
      }
    };

    es.onerror = () => {
      // readyState CONNECTING (0) = EventSource is mid auto-reconnect after a
      // transient drop (e.g. Nginx 1h proxy_read_timeout cut, brief network
      // loss, backend rolling restart). Let it reconnect — the backend keeps
      // the job alive and re-emits a DB snapshot on each new connection.
      if (es.readyState === EventSource.CONNECTING) {
        return;
      }
      // CLOSED (2) or a hard failure we can't recover from. If the job already
      // reached a terminal state, stay closed. Otherwise a non-reconnecting
      // close (rare for EventSource, but possible on 401/403/404/auth-failure)
      // is a dead end — close so we don't tight-loop.
      if (terminalRef.current) {
        close();
      }
    };

    return () => {
      close();
    };
  }, [jobId, queryClient]);
}
