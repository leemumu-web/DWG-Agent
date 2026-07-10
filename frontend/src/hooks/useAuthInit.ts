import { useEffect, useState } from 'react';
import axios from 'axios';
import { useAuthStore } from '../stores/auth.store';
import { refreshSession } from '../api/auth.api';
import type { LoginResponse } from '../types/auth';

let bootstrapRefresh: Promise<LoginResponse> | null = null;

function refreshOnce(): Promise<LoginResponse> {
  if (!bootstrapRefresh) {
    bootstrapRefresh = refreshSession().finally(() => {
      bootstrapRefresh = null;
    });
  }
  return bootstrapRefresh;
}

/**
 * On app mount, restore the tab session from the HttpOnly refresh cookie.
 * Explicit auth failures clear stale tab state; transient server/network
 * failures keep it so a later request can retry through the interceptor.
 *
 * This prevents the "page refresh → 401 → error state flash" issue.
 */
export function useAuthInit() {
  const [ready, setReady] = useState(false);
  const setSession = useAuthStore((s) => s.setSession);
  const clearSession = useAuthStore((s) => s.clearSession);

  useEffect(() => {
    let cancelled = false;
    refreshOnce()
      .then((data) => {
        if (cancelled) return;
        setSession(data.access_token, data.user);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (axios.isAxiosError(error) && error.response?.status === 401) {
          clearSession();
        }
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });

    return () => { cancelled = true; };
  }, [clearSession, setSession]);

  return ready;
}
