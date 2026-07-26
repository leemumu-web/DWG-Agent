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
  status?: number;
  code?: string;
  requestId?: string;
  failure?: ExcelInputFailure;
  workflowId?: number;
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
  403: '当前账号没有执行此操作的权限。如需提权，请联系管理员',
  404: '请求的资源不存在或已被删除',
  405: '当前服务尚未提供这项操作，请联系管理员更新并重新启动服务',
  408: '请求处理超时，请稍后重试',
  409: '当前数据状态与操作冲突，请刷新后重试',
  413: '文件或请求内容超过服务器允许的大小',
  415: '文件类型或请求格式不受支持',
  422: '请求参数未通过校验',
  429: '操作过于频繁，请稍后重试',
};

const FIELD_LABELS: Record<string, string> = {
  batch_name: '批次名称',
  batch_names: '文件夹名称',
  file: '文件',
  files: '文件',
  folder: '文件夹',
  name: '名称',
  password: '密码',
  project_code: '项目编号',
  project_id: '生产项目',
  project_name: '项目名称',
  username: '用户名',
  workflow_id: '生产流程',
};

const CODE_MESSAGES: Record<string, string> = {
  DXF_SPLIT_ATTEMPTS_EXHAUSTED: '本批拆板已达到允许尝试次数，请下载未处理图纸并联系负责人。',
  DXF_SPLIT_INPUT_REQUIRED: '没有可供拆板的分类后图纸，请先完成图纸分类并核对数量。',
  DXF_SPLIT_SOURCE_MISSING: '分类后的拆板图纸已缺失，请返回图纸分类阶段核对并重新确认。',
  DXF_SPLIT_WORKFLOW_EXECUTION_REQUIRED: '拆板必须从所属生产项目重新处理，不能在任务列表中单独重试。',
  FILE_EMPTY: '上传的文件没有内容，请重新选择正确文件。',
  FILE_NOT_DWG: '所选文件不是有效的 DWG 图纸，请核对文件后重新上传。',
  FILE_TOO_LARGE: '文件超过服务器允许的大小，请拆分后重新上传。',
  FILE_TYPE_NOT_ALLOWED: '该文件类型不在当前步骤的接收范围内，请按页面要求重新选择。',
  INPUT_DWG_FOLDER_ALREADY_IMPORTED: '当前生产批次已经导入过 DWG 文件夹；如需更换，请先清空当前输入后再重新选择。',
  INPUT_DWG_FOLDER_FILE_TYPE_NOT_ALLOWED: '所选文件夹中包含非 DWG 文件；请按页面提示确认忽略其他文件，或重新选择只含 DWG 的文件夹。',
  INPUT_EXCEL_FILE_TYPE_NOT_ALLOWED: '生产输入表只能是一个 .xls 或 .xlsx 文件，请重新选择。',
  INPUT_FOLDER_DUPLICATE_DRAWING_NAME: '文件夹中存在同名 DWG；即使位于不同子目录也不能同时导入，请重命名后重试。',
  INPUT_FOLDER_DUPLICATE_PATH: '文件夹中存在重复路径的文件，请整理或重命名后重新选择。',
  INPUT_FOLDER_DWG_REQUIRED: '所选文件夹中没有 DWG 图纸，请重新选择正确的图纸文件夹。',
  INPUT_FOLDER_MANIFEST_INVALID: '浏览器没有完整保留文件夹结构，或所选内容不是一个完整文件夹；请重新点击“选择 DWG 文件夹”。',
  INPUT_FOLDER_ROOT_MISMATCH: '本次选择混入了多个根文件夹，请一次只选择一个完整图纸文件夹。',
  INPUT_FOLDER_TOO_MANY_FILES: '文件夹超过 1000 个文件；系统只接收浏览器顺序中的前 1000 个，请拆分文件夹后核对数量。',
  INPUT_FILE_TYPE_NOT_ALLOWED: '生产输入只接收 DWG 图纸和一个 Excel 文件，请按当前入口重新选择。',
  INPUT_RESTORE_FILE_UNAVAILABLE: '最近清空中的源文件已不完整，系统没有猜测替代文件；请重新上传完整 Excel 和 DWG 文件夹。',
  INPUT_RESTORE_NOT_AVAILABLE: '当前没有可精确恢复的清空记录，或本批已经重新上传了文件。',
  IDENTITY_TABLE_READ_ONLY: '用户、角色和登录安全表只能查看，不能从数据控制台直接修改；请使用用户管理页面。',
  SUPER_ADMIN_ACCOUNT_PROTECTED: '唯一的超级管理员账号受系统保护，不能删除、禁用、降级或由他人重置密码。',
  SUPER_ADMIN_ASSIGNMENT_FORBIDDEN: '管理员不能授予超级管理员权限；系统只保留一个超级管理员账号。',
  SUPER_ADMIN_ROLE_PROTECTED: '超级管理员角色是系统保护角色，不能修改其权限。',
  SUPER_ADMIN_SINGLETON: '系统只能有一个超级管理员，不能再向其他账号授予该角色。',
  USERNAME_EXISTS: '用户名已存在，请更换用户名后重新创建。',
  WORKFLOW_STAGE_INPUT_INCOMPLETE: '当前阶段缺少必需的上一步结果，请返回前序阶段补齐并确认。',
};

const TECHNICAL_MESSAGE = new RegExp([
  'traceback',
  'exception',
  'sqlalchemy',
  'pymysql',
  'stack[ -]?trace',
  'operationalerror',
  'integrityerror',
  '\\b[a-z][a-z0-9_.]*(?:error|exception)\\b',
  'file\\s+"',
  'line\\s+\\d+',
  '\\berrno\\b',
  'connection(?:refused|reset|pool)',
  '\\b(?:fastapi|uvicorn|celery|redis|minio|docker)\\b',
  'https?://',
  '\\blocalhost\\b',
  '\\bhttp\\s*[45]\\d{2}\\b',
  '\\bat\\s+[^ ]+\\(',
  '\\/app\\/',
  '\\/home\\/',
  '\\.py(?::|\\b)',
  '\\b(?:select|insert|update|delete)\\s+.+\\b(?:from|into|set)\\b',
].join('|'), 'i');
const HAS_CHINESE = /[\u3400-\u9fff]/;
const DISPLAYED_ERROR_CODE = /\s*[[(][A-Z][A-Z0-9_:-]{2,}[\])]/g;
const BARE_ERROR_CODE = /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g;

function codeMessage(code?: string): string | undefined {
  if (!code) return undefined;
  if (CODE_MESSAGES[code]) return CODE_MESSAGES[code];
  if (code.startsWith('DXF_CLASSIFICATION_')) {
    return '图纸分类未完成，请进入所属生产项目核对输入图纸和分类结果。';
  }
  if (code.startsWith('DXF_SPLIT_')) {
    return '图纸拆板未完成，请进入所属生产项目查看需要处理的图纸。';
  }
  if (code.startsWith('EXCEL_')) {
    return 'Excel 整理未完成，请核对源表、拆板结果和页面列出的输入问题。';
  }
  if (code.startsWith('WORKFLOW_')) {
    return '当前生产流程状态不允许这项操作，请刷新项目并按当前阶段继续。';
  }
  if (code.startsWith('STORAGE_') || code.startsWith('OBJECT_')) {
    return '文件存储暂时无法读取或处理，请稍后刷新重试。';
  }
  if (code.startsWith('FILE_') || code.startsWith('UPLOAD_')) {
    return '文件未能处理，请核对文件类型、内容和当前步骤后重新操作。';
  }
  if (code.startsWith('JOB_')) {
    return '处理任务未能完成，请刷新任务状态后按页面建议处理。';
  }
  if (code.startsWith('AUTH_') || code.startsWith('TOKEN_')) {
    return '登录状态或账号权限不符合要求，请重新登录后再试。';
  }
  return undefined;
}

function safeOperatorText(value: unknown, maximum = 500): string | undefined {
  const text = boundedText(value, maximum);
  if (!text || !HAS_CHINESE.test(text) || TECHNICAL_MESSAGE.test(text)) return undefined;
  return text
    .replace(DISPLAYED_ERROR_CODE, '')
    .replace(BARE_ERROR_CODE, '')
    .replace(/对象存储/g, '文件存储')
    .replace(/\s{2,}/g, ' ')
    .trim() || undefined;
}

/** Turn a persisted task/stage failure into bounded Chinese operator feedback. */
export function operatorErrorMessage(
  code?: string | null,
  message?: string | null,
  fallback = '处理未完成，请刷新状态后重试。',
): string {
  return safeOperatorText(message) || codeMessage(code ?? undefined) || fallback;
}

function fieldPath(location: Array<string | number> = []): string {
  const meaningful = location.filter((item) => !['body', 'query', 'path', 'header'].includes(String(item)));
  const field = [...meaningful].reverse().find((item) => typeof item === 'string');
  return field ? (FIELD_LABELS[field] ?? '填写内容') : '填写内容';
}

function validationReason(issue: ValidationIssue): string {
  const safe = safeOperatorText(issue.msg, 160);
  if (safe) return safe;
  const message = issue.msg?.toLowerCase() ?? '';
  if (message.includes('required') || message.includes('missing')) return '不能为空';
  if (message.includes('too long') || message.includes('at most')) return '数量或长度超过限制';
  if (message.includes('too short') || message.includes('at least')) return '数量或长度不足';
  return '格式不正确';
}

function validationMessage(body: ErrorBody | undefined): string | undefined {
  const issues = body?.error?.details?.errors
    ?? (Array.isArray(body?.detail) ? body.detail : undefined);
  if (!issues?.length) return undefined;
  const summaries = issues.slice(0, 3).map((issue) => {
    const field = fieldPath(issue.loc);
    const reason = validationReason(issue);
    return reason.includes(field) ? reason : `${field}：${reason}`;
  });
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
  const rawMessage = boundedText(candidate.message, 500);
  const rawAction = boundedText(candidate.action, 500);
  const contractVersion = candidate.contract_version;
  if (!code || !rawMessage || !rawAction || !Number.isInteger(contractVersion)) return undefined;
  const message = operatorErrorMessage(
    code,
    rawMessage,
    'Excel 输入未通过检查，请按下列问题修正后重新上传。',
  );
  const action = safeOperatorText(rawAction)
    || '按下列工作表、行和字段逐项修正后重新上传。';

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
      reason: safeOperatorText(issue.reason, 120) ?? '内容不符合输入规范',
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

function suffix(requestId?: string): string {
  return requestId ? `（请求编号 ${requestId}）` : '';
}

function bodyMessage(body: ErrorBody | undefined, code?: string): string | undefined {
  const validation = validationMessage(body);
  if (validation) return validation;
  return operatorErrorMessage(
    code,
    body?.error?.message
      || (typeof body?.detail === 'string' ? body.detail : undefined)
      || body?.message,
    '',
  ) || undefined;
}

function parsedResponseError(
  body: ErrorBody | undefined,
  status: number,
  headers: Record<string, unknown> | undefined,
  fallback: string,
): ParsedApiError {
  const code = boundedText(body?.error?.code, 120);
  const requestId = requestIdOf(body, headers);
  const baseMessage = bodyMessage(body, code)
    || STATUS_MESSAGES[status]
    || (status >= 500 ? '服务器处理失败，请稍后重试' : `${fallback}（HTTP ${status}）`);
  return {
    message: `${baseMessage}${suffix(requestId)}`,
    status,
    code,
    requestId,
    failure: parseExcelInputFailure(body?.error?.details?.failure),
    workflowId: Number.isInteger(body?.error?.details?.workflow_id)
      && Number(body?.error?.details?.workflow_id) > 0
      ? Number(body?.error?.details?.workflow_id)
      : undefined,
  };
}

/** Give an operator one bounded next action without exposing response internals. */
export function apiErrorRecovery(error: ParsedApiError): string {
  if (error.code === 'WORKFLOW_STAGE_INPUT_INCOMPLETE') {
    return '返回前序阶段补齐必需产物，再回到当前阶段重新检查。';
  }
  if (error.code === 'DXF_SPLIT_SOURCE_MISSING') {
    return '进入所属生产项目，返回图纸分类阶段核对文件数量，重新确认后再执行拆板。';
  }
  if (error.code === 'DXF_SPLIT_INPUT_REQUIRED') {
    return '先完成图纸分类并处理待确认项，再回到拆板阶段。';
  }
  if (error.code === 'DXF_SPLIT_ATTEMPTS_EXHAUSTED') {
    return '不要连续重试；下载本批未处理图纸，交给负责人核对后再决定处理方式。';
  }
  if (error.code === 'DXF_SPLIT_WORKFLOW_EXECUTION_REQUIRED') {
    return '进入所属生产项目，在拆板阶段按最新输入重新处理。';
  }
  if (['WORKFLOW_RETENTION_NOT_TERMINAL', 'WORKFLOW_RETENTION_ACTIVE_STAGE', 'WORKFLOW_RETENTION_ACTIVE_JOB'].includes(error.code ?? '')) {
    return '等待本批流程和任务完全结束，再重新执行完整备份预检。';
  }
  if (error.code === 'WORKFLOW_RETENTION_SHARED_FILES') {
    return '这些文件仍被其他生产批次引用；不要删除，请联系管理员核对生产关系。';
  }
  if (['WORKFLOW_RETENTION_FILE_REGISTRATION_MISSING', 'WORKFLOW_RETENTION_FILES_UNAVAILABLE', 'WORKFLOW_RETENTION_MANIFEST_STALE'].includes(error.code ?? '')) {
    return '停止清理并保留请求编号，请管理员先核对文件登记关系或重新生成完整备份。';
  }
  if (['WORKFLOW_RETENTION_OBJECT_MISSING', 'WORKFLOW_RETENTION_OBJECT_MISMATCH'].includes(error.code ?? '')) {
    return '文件与系统登记不一致，未执行删除；请管理员先运行存储一致性检查。';
  }
  if (['WORKFLOW_RETENTION_ENQUEUE_FAILED', 'WORKFLOW_RETENTION_STORAGE_UNAVAILABLE'].includes(error.code ?? '')) {
    return '服务器文件仍完整保留；等待后台处理和文件存储恢复后再重试一次。';
  }
  if (['WORKFLOW_RETENTION_PURGE_FAILED', 'WORKFLOW_RETENTION_PURGE_PARTIAL', 'WORKFLOW_RETENTION_METADATA_COMMIT_FAILED'].includes(error.code ?? '')) {
    return '不要重新上传或手工删除对象；保留请求编号，由管理员核对补偿流水后从当前备份重试。';
  }
  if (error.status === 401) return '重新登录后再执行；不要连续重复提交。';
  if (error.status === 403) return '确认当前账号属于该项目；如需提权，请联系管理员。';
  if (error.status === 404) return '刷新当前页面确认批次是否仍存在；若已切换 attempt，请使用最新批次。';
  if (error.status === 409) return '先刷新当前状态，再按页面显示的最新阶段重新操作。';
  if (error.status === 413) return '减少单次文件数量或文件大小，再重新提交。';
  if (error.status === 415) return '核对文件扩展名和真实格式，改用页面支持的文件后重试。';
  if (error.status === 422) return '按错误中指出的字段、工作表或文件逐项修正后重新提交。';
  if (error.status === 429) return '停止连续点击，等待一分钟后刷新状态再操作。';
  if (error.status !== undefined && error.status >= 500) {
    return '服务器暂时无法完成操作，请保留请求编号，稍后重试一次；若重复出现，请联系管理员。';
  }
  if (error.message.startsWith('请求超时')) {
    return '先刷新任务状态，确认服务器是否已受理；只有确认未受理时才重新提交。';
  }
  return '检查网络连接和服务状态后重试；若重复出现，请把请求编号交给管理员。';
}

/** Parse an API failure without exposing arbitrary response properties. */
export function parseApiError(error: unknown, fallback = '操作失败'): ParsedApiError {
  if (!axios.isAxiosError(error)) {
    return {
      message: operatorErrorMessage(
        undefined,
        error instanceof Error ? error.message : undefined,
        fallback,
      ),
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
