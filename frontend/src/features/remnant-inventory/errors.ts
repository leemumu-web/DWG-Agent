import axios from 'axios';
import { describeApiError, describeApiErrorAsync } from '../../shared/api';

const REMNANT_MESSAGES: Record<string, string> = {
  REMNANT_THICKNESS_REQUIRED: '请填写余料厚度',
  REMNANT_MATERIAL_REQUIRED: '请选择或新建材质',
  REMNANT_PROJECT_REQUIRED: '请填写项目编号',
  REMNANT_PARTS_REQUIRED: '至少填写一个零件编号',
  REMNANT_DXF_REQUIRED: '缺少可用于确认的 DXF 图纸',
  REMNANT_IMPORT_ITEM_NOT_READY: '该图纸当前不能确认，请刷新后重试',
  REMNANT_IMPORT_ITEM_NOT_FOUND: '导入图纸不存在或已被删除',
  REMNANT_PARSE_FAILED: '图纸解析失败，请重试或联系管理员',
  REMNANT_CONVERSION_FAILED: 'DWG 图纸转换失败，请重试或联系管理员',
};

const WARNING_TITLES: Record<string, string> = {
  MATERIAL_CANDIDATES_CONFLICT: '材质候选需要确认',
  PROJECT_CANDIDATES_CONFLICT: '项目标题候选需要确认',
  PART_CANDIDATES_CONFLICT: '零件编号候选需要确认',
  UNRECOGNIZED_TEXT: '图纸存在需要人工确认的文字',
};

const FIELD_NAMES: Record<string, string> = {
  thickness_mm: '厚度', material_id: '材质', project_no: '项目编号',
  parts: '零件编号', remnant_ids: '余料', item_ids: '图纸',
};

export function describeRemnantCode(code: string, fallback = '操作未完成'): string {
  return REMNANT_MESSAGES[code] ?? fallback;
}

export function warningTitle(code: string): string {
  return WARNING_TITLES[code] ?? '图纸存在需要人工确认的问题';
}

function validationDetails(error: unknown): string | undefined {
  if (!axios.isAxiosError(error)) return undefined;
  const issues = error.response?.data?.error?.details?.errors;
  if (!Array.isArray(issues) || issues.length === 0) return undefined;
  const descriptions = issues.slice(0, 3).map((issue: { loc?: unknown[]; type?: string }) => {
    const location = issue.loc ?? [];
    const field = String(location[location.length - 1] ?? '');
    const type = String(issue.type ?? '');
    const reason = type.includes('missing') ? '不能为空'
      : type.includes('too_long') || type.includes('max_length') ? '数量超过限制'
        : type.includes('too_short') || type.includes('min_length') ? '至少需要一项'
          : '格式不正确';
    return `${FIELD_NAMES[field] ?? '请求内容'}${reason}`;
  });
  return `请求参数错误：${descriptions.join('；')}`;
}

export function describeRemnantError(error: unknown, fallback: string): string {
  const validation = validationDetails(error);
  if (validation) return validation;
  return sanitizeMessage(describeApiError(error, fallback), fallback);
}

function sanitizeMessage(message: string, fallback: string): string {
  const sanitized = message.replace(/\s*\[REMNANT_[A-Z0-9_]+\]/g, '');
  if (/REMNANT_[A-Z0-9_]+/.test(sanitized)) return fallback;
  return /[\u3400-\u9fff]/.test(sanitized) ? sanitized : fallback;
}

export async function describeRemnantErrorAsync(error: unknown, fallback: string): Promise<string> {
  return sanitizeMessage(await describeApiErrorAsync(error, fallback), fallback);
}
