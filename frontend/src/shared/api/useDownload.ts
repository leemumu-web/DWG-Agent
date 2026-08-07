import { useCallback, useEffect, useRef, useState } from 'react';

export interface DownloadHandle {
  signal: AbortSignal;
  /** Release this download's ownership of the shared slot. Only the request that
   *  currently owns the slot is released; a stale request that was superseded by
   *  a newer download is a no-op, so a late settle can never cancel the newer one. */
  finish: () => void;
}

export interface DownloadControl {
  /** True while the request that currently owns the slot is in flight. */
  active: boolean;
  /** Cancel the previous in-flight download and take ownership for a new one.
   *  Pass the returned handle's `signal` into `downloadBlob`; call `handle.finish()`
   *  when that download settles. */
  start: () => DownloadHandle;
  /** Abort the current in-flight download, if any. */
  cancel: () => void;
}

/**
 * Manage a single cancellable blob download per component. `start()` aborts any
 * previous in-flight download and returns an ownership handle: the component has
 * exactly one live download at a time, and a stale download's late settle cannot
 * clear the slot that a newer download owns. Abort also fires on unmount so a
 * download never outlives its page.
 */
export function useDownload(): DownloadControl {
  const controllerRef = useRef<AbortController | null>(null);
  const [active, setActive] = useState(false);

  const start = useCallback(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setActive(true);
    return {
      signal: controller.signal,
      finish: () => {
        if (controllerRef.current === controller) {
          controllerRef.current = null;
          setActive(false);
        }
      },
    };
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

  return { active, start, cancel };
}
