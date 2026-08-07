import type { AxiosProgressEvent, Method } from 'axios';

import { apiClient } from './client';
import { describeApiErrorAsync } from './error';

export interface TransferProgress {
  loadedBytes: number;
  totalBytes?: number;
  percent?: number;
  completed: boolean;
  totalIsEstimated: boolean;
  /** Download-only: true while the request is sent but the first body byte has
   *  not arrived yet (the server is still generating the payload). Uploads and
   *  completed progress never set this, so the bar can render a "preparing"
   *  state instead of a stuck 0% bar. */
  preparing?: boolean;
}

export type TransferProgressHandler = (progress: TransferProgress) => void;

export function initialTransferProgress(totalBytes?: number): TransferProgress {
  return {
    loadedBytes: 0,
    totalBytes,
    percent: totalBytes && totalBytes > 0 ? 0 : undefined,
    completed: false,
    totalIsEstimated: Boolean(totalBytes),
  };
}

export function transferProgressFromAxios(
  event: AxiosProgressEvent,
  expectedBytes?: number,
): TransferProgress {
  const measuredTotal = event.total && event.total > 0 ? event.total : undefined;
  const totalBytes = measuredTotal ?? expectedBytes;
  const loadedBytes = Math.max(0, event.loaded);
  const calculated = totalBytes && totalBytes > 0
    ? Math.round((loadedBytes / totalBytes) * 100)
    : event.progress === undefined
      ? undefined
      : Math.round(event.progress * 100);
  return {
    loadedBytes,
    totalBytes,
    percent: calculated === undefined ? undefined : Math.min(99, Math.max(0, calculated)),
    completed: false,
    totalIsEstimated: measuredTotal === undefined && totalBytes !== undefined,
  };
}

export function completedTransferProgress(
  loadedBytes: number,
  totalBytes?: number,
): TransferProgress {
  const completedBytes = Math.max(0, loadedBytes);
  return {
    loadedBytes: completedBytes,
    totalBytes: totalBytes ?? completedBytes,
    percent: 100,
    completed: true,
    totalIsEstimated: false,
  };
}

/** True when the request was intentionally aborted (user cancelled the download)
 *  rather than failing. Axios surfaces this as a CanceledError. */
export function isDownloadCancelled(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const candidate = error as {
    code?: string;
    name?: string;
    __CANCEL__?: boolean;
  };
  return candidate.code === 'ERR_CANCELED'
    || candidate.name === 'CanceledError'
    || candidate.__CANCEL__ === true;
}

export function triggerBlobDownload(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.rel = 'noopener';
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  setTimeout(() => {
    anchor.remove();
    URL.revokeObjectURL(objectUrl);
  }, 100);
}

function responseFilename(
  headers: unknown,
  fallbackName: string,
): string {
  const headerMap = headers as {
    get?: (name: string, matcher?: true) => unknown;
    [key: string]: unknown;
  };
  const disposition = (
    typeof headerMap.get === 'function'
      ? headerMap.get('content-disposition')
      : headerMap['content-disposition']
  );
  if (typeof disposition !== 'string') return fallbackName;
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return fallbackName;
    }
  }
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? fallbackName;
}

export async function downloadBlob({
  url,
  fallbackName,
  errorMessage,
  method = 'GET',
  data,
  expectedBytes,
  onProgress,
  timeout = 300_000,
  signal,
}: {
  url: string;
  fallbackName: string;
  errorMessage: string;
  method?: Method;
  data?: unknown;
  expectedBytes?: number;
  onProgress?: TransferProgressHandler;
  timeout?: number;
  signal?: AbortSignal;
}): Promise<void> {
  onProgress?.({ ...initialTransferProgress(expectedBytes), preparing: true });
  try {
    const response = await apiClient.request<Blob>({
      url,
      method,
      data,
      responseType: 'blob',
      timeout,
      signal,
      onDownloadProgress: (event) => {
        onProgress?.(transferProgressFromAxios(event, expectedBytes));
      },
    });
    const filename = responseFilename(response.headers, fallbackName);
    onProgress?.(completedTransferProgress(response.data.size, response.data.size));
    triggerBlobDownload(response.data, filename);
  } catch (error) {
    // Cancellation must surface as-is so components can distinguish a user
    // abort from a real failure; wrapping it would turn it into a network error.
    if (isDownloadCancelled(error)) throw error;
    throw new Error(await describeApiErrorAsync(error, errorMessage));
  }
}
