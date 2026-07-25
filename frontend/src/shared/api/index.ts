export { apiClient, fetchAllPages } from './client';
export type { ApiEnvelope, PageEnvelope } from './client';
export {
  apiErrorRecovery,
  describeApiError,
  describeApiErrorAsync,
  enrichApiError,
  parseApiError,
} from './error';
export type {
  ExcelInputFailure,
  ExcelInputIssue,
  ParsedApiError,
} from './error';
