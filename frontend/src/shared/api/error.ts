import axios from 'axios';

interface ValidationIssue {
  loc?: Array<string | number>;
  msg?: string;
}

export interface ExcelInputIssue {
  sheet: string | null;
  row: number | null;
  column: string | null;
  field: string | null;
  value: string | null;
  reason: string;
}

export interface ExcelInputFailure {
  code: string;
  message: string;
  action: string;
  contractVersion: number;
  issues: ExcelInputIssue[];
  sheets: string[];
}

export interface ParsedApiError {
  message: string;
  code?: string;
  requestId?: string;
  failure?: ExcelInputFailure;
}

interface ErrorBody {
  error?: {
    code?: string;
    message?: string;
    details?: { errors?: ValidationIssue[]; failure?: unknown } & Record<string, unknown>;
  };
  detail?: string | ValidationIssue[];
  message?: string;
  meta?: { request_id?: string };
}

const MAX_FAILURE_ISSUES = 20;
const MAX_FAILURE_SHEETS = 10;

const STATUS_MESSAGES: Record<number, string> = {
  400: '请求内容不正确',
  401: '登录状态已失效，请重新登录',
  403: '当前账号没有执行此操作的权限',
  404: '请求的资源不存在或已被删除',
  405: '当前服务不支持此操作，可能仍在运行旧版后端，请联系管理员重启服务',
  408: '请求处理超时，请稍后重试',
  409: '当前数据状态与操作冲突，请刷新后重试',
  413: '文件或请求内容超过服务器允许的大小',
  415: '文件类型或请求格式不受支持',
  422: '请求参数未通过校验',
  429: '操作过于频繁，请稍后重试',
};

function fieldPath(location: Array<string | number> = []): string {
  const meaningful = location.filter((item) => !['body', 'query', 'path', 'header'].includes(String(item)));
  return meaningful.reduce<string>((path, item) => {
    if (typeof item === 'number') return `${path}[${item}]`;
    return path ? `${path}.${item}` : item;
  }, '') || '请求';
}

function validationMessage(body: ErrorBody | undefined): string | undefined {
  const issues = body?.error?.details?.errors
    ?? (Array.isArray(body?.detail) ? body.detail : undefined);
  if (!issues?.length) return undefined;
  const summaries = issues.slice(0, 3).map((issue) => (
    `${fieldPath(issue.loc)}：${issue.msg || '参数无效'}`
  ));
  if (issues.length > 3) summaries.push(`另有 ${issues.length - 3} 项`);
  return `请求参数错误：${summaries.join('；')}`;
}

function boundedText(value: unknown, maximum: number): string | undefined {
  if (typeof value !== 'string') return undefined;
  const normalized = value.replace(/[\r\n\t]+/g, ' ').trim();
  if (!normalized) return undefined;
  return normalized.length <= maximum ? normalized : `${normalized.slice(0, maximum - 1)}…`;
}

function nullableText(value: unknown, maximum = 160): string | null {
  return boundedText(value, maximum) ?? null;
}

function parseExcelInputFailure(value: unknown): ExcelInputFailure | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const candidate = value as Record<string, unknown>;
  const code = boundedText(candidate.code, 120);
  const message = boundedText(candidate.message, 500);
  const action = boundedText(candidate.action, 500);
  const contractVersion = candidate.contract_version;
  if (!code || !message || !action || !Number.isInteger(contractVersion)) return undefined;

  const rawIssues = Array.isArray(candidate.issues)
    ? candidate.issues.slice(0, MAX_FAILURE_ISSUES)
    : [];
  const issues = rawIssues.flatMap((value): ExcelInputIssue[] => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    const issue = value as Record<string, unknown>;
    const row = Number.isInteger(issue.row) && Number(issue.row) > 0
      ? Number(issue.row)
      : null;
    return [{
      sheet: nullableText(issue.sheet, 120),
      row,
      column: nullableText(issue.column, 40),
      field: nullableText(issue.field, 120),
      value: nullableText(issue.value),
      reason: boundedText(issue.reason, 120) ?? '',
    }];
  });
  const sheets = Array.isArray(candidate.sheets)
    ? candidate.sheets
      .slice(0, MAX_FAILURE_SHEETS)
      .flatMap((sheet) => {
        const text = boundedText(sheet, 120);
        return text ? [text] : [];
      })
    : [];
  return {
    code,
    message,
    action,
    contractVersion: Number(contractVersion),
    issues,
    sheets,
  };
}

function requestIdOf(
  body: ErrorBody | undefined,
  headers?: Record<string, unknown>,
): string | undefined {
  return boundedText(
    body?.meta?.request_id
      || headers?.['x-request-id']
      || headers?.['X-Request-ID'],
    128,
  );
}

function suffix(code?: string, requestId?: string): string {
  const codePart = code && !['ERROR', 'HTTP_ERROR'].includes(code) ? ` [${code}]` : '';
  const requestPart = requestId ? `（请求 ${requestId}）` : '';
  return `${codePart}${requestPart}`;
}

function bodyMessage(body: ErrorBody | undefined): string | undefined {
  const validation = validationMessage(body);
  if (validation) return validation;
  if (body?.error?.message) return body.error.message;
  if (typeof body?.detail === 'string') return body.detail;
  return body?.message;
}

function parsedResponseError(
  body: ErrorBody | undefined,
  status: number,
  headers: Record<string, unknown> | undefined,
  fallback: string,
): ParsedApiError {
  const code = boundedText(body?.error?.code, 120);
  const requestId = requestIdOf(body, headers);
  const baseMessage = bodyMessage(body)
    || STATUS_MESSAGES[status]
    || (status >= 500 ? '服务器处理失败，请稍后重试' : `${fallback}（HTTP ${status}）`);
  return {
    message: `${baseMessage}${suffix(code, requestId)}`,
    code,
    requestId,
    failure: parseExcelInputFailure(body?.error?.details?.failure),
  };
}

/** Parse an API failure without exposing arbitrary response properties. */
export function parseApiError(error: unknown, fallback = '操作失败'): ParsedApiError {
  if (!axios.isAxiosError(error)) {
    return {
      message: error instanceof Error && error.message ? error.message : fallback,
    };
  }

  const response = error.response;
  if (!response) {
    return {
      message: error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT'
        ? '请求超时，服务器仍可能在处理，请先刷新状态再决定是否重试'
        : '无法连接服务器，请检查网络或确认服务已启动',
    };
  }
  const body = response.data && !(response.data instanceof Blob)
    ? response.data as ErrorBody
    : undefined;
  return parsedResponseError(
    body,
    response.status,
    response.headers as Record<string, unknown>,
    fallback,
  );
}

/** Convert every API/network failure into a concrete, user-facing message. */
export function describeApiError(error: unknown, fallback = '操作失败'): string {
  return parseApiError(error, fallback).message;
}

/** Blob downloads can still carry the normal JSON error envelope. */
export async function describeApiErrorAsync(error: unknown, fallback = '操作失败'): Promise<string> {
  if (axios.isAxiosError(error) && error.response?.data instanceof Blob) {
    try {
      const parsed = JSON.parse(await error.response.data.text()) as ErrorBody;
      return parsedResponseError(
        parsed,
        error.response.status,
        error.response.headers as Record<string, unknown>,
        fallback,
      ).message;
    } catch {
      // Fall through to status/network handling without exposing raw response data.
    }
  }
  return describeApiError(error, fallback);
}

/** Preserve Axios metadata while making existing Error.message call sites specific. */
export async function enrichApiError(error: unknown): Promise<unknown> {
  if (axios.isAxiosError(error)) error.message = await describeApiErrorAsync(error);
  return error;
}
