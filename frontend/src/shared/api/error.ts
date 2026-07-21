import axios from 'axios';

interface ValidationIssue {
  loc?: Array<string | number>;
  msg?: string;
}

interface ErrorBody {
  error?: {
    code?: string;
    message?: string;
    details?: { errors?: ValidationIssue[] } & Record<string, unknown>;
  };
  detail?: string | ValidationIssue[];
  message?: string;
  meta?: { request_id?: string };
}

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

function suffix(body: ErrorBody | undefined, headers?: Record<string, unknown>): string {
  const code = body?.error?.code;
  const requestId = body?.meta?.request_id
    || String(headers?.['x-request-id'] || headers?.['X-Request-ID'] || '');
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

/** Convert every API/network failure into a concrete, user-facing message. */
export function describeApiError(error: unknown, fallback = '操作失败'): string {
  if (!axios.isAxiosError(error)) {
    return error instanceof Error && error.message ? error.message : fallback;
  }

  const response = error.response;
  if (!response) {
    if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
      return '请求超时，服务器仍可能在处理，请先刷新状态再决定是否重试';
    }
    return '无法连接服务器，请检查网络或确认服务已启动';
  }

  const body = response.data && !(response.data instanceof Blob)
    ? response.data as ErrorBody
    : undefined;
  const status = response.status;
  const message = bodyMessage(body)
    || STATUS_MESSAGES[status]
    || (status >= 500 ? '服务器处理失败，请稍后重试' : `${fallback}（HTTP ${status}）`);
  return `${message}${suffix(body, response.headers as Record<string, unknown>)}`;
}

/** Blob downloads can still carry the normal JSON error envelope. */
export async function describeApiErrorAsync(error: unknown, fallback = '操作失败'): Promise<string> {
  if (axios.isAxiosError(error) && error.response?.data instanceof Blob) {
    try {
      const parsed = JSON.parse(await error.response.data.text()) as ErrorBody;
      const status = error.response.status;
      const message = bodyMessage(parsed)
        || STATUS_MESSAGES[status]
        || (status >= 500 ? '服务器处理失败，请稍后重试' : `${fallback}（HTTP ${status}）`);
      return `${message}${suffix(parsed, error.response.headers as Record<string, unknown>)}`;
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
