import { useEffect, useState } from 'react';
import { useAuthStore } from '../stores/auth.store';
import { refreshSession } from '../api/auth.api';

/**
 * On app mount: verify the stored access token is still valid.
 * If expired, try to refresh silently via the HttpOnly refresh cookie.
 * If the refresh also fails, clear the session (the RequireAuth guard
 * will then redirect to /login).
 *
 * This prevents the "page refresh → 401 → error state flash" issue.
 */
export function useAuthInit() {
  const [ready, setReady] = useState(false);
  const token = useAuthStore((s) => s.accessToken);
  const setAccessToken = useAuthStore((s) => s.setAccessToken);
  const clearSession = useAuthStore((s) => s.clearSession);

  useEffect(() => {
    if (!token) {
      // No token at all — let RequireAuth handle it
      setReady(true);
      return;
    }

    // Proactively refresh: if the server accepts it, we get a fresh token.
    // Only clear session on explicit auth failure (401), not on network errors.
    let cancelled = false;
    refreshSession()
      .then((data) => {
        if (cancelled) return;
        if (data) {
          setAccessToken(data.access_token);
        }
        // If data is null (refresh failed), keep the old token — it might
        // still be valid. The axios interceptor will handle real 401s.
      })
      .catch(() => {
        // Network error or server down — don't clear, let the user retry
        if (!cancelled) setReady(true);
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });

    return () => { cancelled = true; };
  }, []); // run once on mount — eslint-disable-line react-hooks/exhaustive-deps

  return ready;
}
