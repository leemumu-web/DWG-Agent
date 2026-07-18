import axios from 'axios';
import { useAuthStore } from '../stores/auth.store';
import { enrichApiError } from './error';

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 30_000,
  withCredentials: true, // send HttpOnly refresh cookie
});

// ── request interceptor: attach access token ────────────────────────────
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ── response interceptor: silent token refresh on 401 ──────────────────
let _refreshPromise: Promise<string | null> | null = null;

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const req = error.config as (typeof error.config & { _retry?: boolean }) | undefined;
    if (!req) return Promise.reject(await enrichApiError(error));

    // Only handle 401; never retry the refresh endpoint itself or login
    const isRefresh = req.url === '/api/v1/auth/tokens/refresh';
    const isLogin = req.url === '/api/v1/auth/sessions';
    if (error.response?.status !== 401 || isRefresh || isLogin || req._retry) {
      return Promise.reject(await enrichApiError(error));
    }

    // De-duplicate concurrent refresh attempts
    if (!_refreshPromise) {
      _refreshPromise = (async () => {
        try {
          // Use plain axios (not apiClient) so we bypass this interceptor
          const res = await axios.post<ApiEnvelope<{ access_token: string; user: unknown }>>(
            `${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/auth/tokens/refresh`,
            undefined,
            { withCredentials: true },
          );
          const token = res.data.data.access_token;
          useAuthStore.getState().setAccessToken(token);
          return token;
        } catch {
          useAuthStore.getState().clearSession();
          // Only redirect if we're not already on the login page
          if (window.location.pathname !== '/login') {
            window.location.href = '/login';
          }
          return null;
        } finally {
          _refreshPromise = null;
        }
      })();
    }

    const newToken = await _refreshPromise;
    if (!newToken) return Promise.reject(await enrichApiError(error));

    // Retry original request with fresh token
    req._retry = true;
    req.headers.Authorization = `Bearer ${newToken}`;
    return apiClient(req);
  },
);

export interface ApiEnvelope<T> {
  data: T;
  meta: {
    request_id: string;
    timestamp?: string;
  };
}

export interface PageEnvelope<T> extends ApiEnvelope<T[]> {
  pagination: {
    page: number;
    page_size: number;
    total: number;
    total_pages?: number;
  };
}

/**
 * Fetch every page of a paginated list endpoint by repeatedly requesting
 * page_size=200 (the backend hard maximum — Query(le=200)) until the total
 * is reached. Returns the concatenated `data` arrays.
 *
 * Use this for in-memory joins that need the full list (e.g. FilesPage's
 * files↔jobs join via params.file_id). For plain list pages, prefer the
 * normal single-page call and let the table paginate.
 */
export async function fetchAllPages<T>(path: string, params?: Record<string, unknown>): Promise<T[]> {
  const PAGE_SIZE = 200;
  const first = await apiClient.get<PageEnvelope<T>>(path, {
    params: { page: 1, page_size: PAGE_SIZE, ...params },
  });
  const firstPage = first.data;
  const acc = [...firstPage.data];
  const total = firstPage.pagination.total;
  // Single page or backend already returned everything
  if (acc.length >= total) return acc;
  const totalPages = firstPage.pagination.total_pages ?? Math.ceil(total / PAGE_SIZE);
  // Guard against absurd totals
  const capped = Math.min(totalPages, 500);
  for (let p = 2; p <= capped; p++) {
    const res = await apiClient.get<PageEnvelope<T>>(path, {
      params: { page: p, page_size: PAGE_SIZE, ...params },
    });
    acc.push(...res.data.data);
    if (res.data.data.length === 0) break; // safety: stop on empty page
  }
  return acc;
}
