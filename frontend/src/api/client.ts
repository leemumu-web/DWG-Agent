import axios from 'axios';
import { useAuthStore } from '../stores/auth.store';

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000',
  timeout: 30_000,
});

apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

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
  };
}
