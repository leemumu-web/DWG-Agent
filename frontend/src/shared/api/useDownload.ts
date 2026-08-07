import { useCallback, useEffect, useRef, useState } from 'react';

export interface DownloadControl {
  /** True while a download request is in flight (started but not finished/cancelled). */
  active: boolean;
  /** Cancel the previous in-flight download and return a fresh AbortSignal. */
  start: () => AbortSignal;
  /** Mark the in-flight download as finished (success or handled failure). */
  finish: () => void;
  /** Abort the in-flight download, if any. */
  cancel: () => void;
}

/**
 * Manage a single cancellable blob download per component. `start()` returns
 * the signal to pass into `downloadBlob`; the component owns exactly one
 * download at a time (starting a new one aborts the previous). The abort is
 * also fired automatically on unmount so a download never outlives its page.
 */
export function useDownload(): DownloadControl {
  const controllerRef = useRef<AbortController | null>(null);
  const [active, setActive] = useState(false);

  const start = useCallback(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setActive(true);
    return controller.signal;
  }, []);

  const finish = useCallback(() => {
    controllerRef.current = null;
    setActive(false);
  }, []);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setActive(false);
  }, []);

  useEffect(() => () => {
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, []);

  return { active, start, finish, cancel };
}
