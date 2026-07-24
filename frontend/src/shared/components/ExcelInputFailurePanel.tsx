import { Alert, Tag } from 'antd';

import type { ExcelInputFailure, ExcelInputIssue } from '../api';
import './ExcelInputFailurePanel.css';

function issueLocation(issue: ExcelInputIssue): string {
  const parts: string[] = [];
  if (issue.sheet) parts.push(issue.sheet);
  if (issue.row) parts.push(`第 ${issue.row} 行`);
  if (issue.column) parts.push(`${issue.column} 列`);
  if (issue.field) parts.push(issue.field);
  return parts.join(' · ') || '表格结构';
}

export interface ExcelInputFailurePanelProps {
  failure: ExcelInputFailure;
  requestId?: string;
}

/** Operator-facing Excel failure. Only renders the bounded public contract. */
export function ExcelInputFailurePanel({
  failure,
  requestId,
}: ExcelInputFailurePanelProps) {
  return (
    <Alert
      className="excel-input-failure"
      type="error"
      showIcon
      role="alert"
      aria-label="表格输入不符合规范"
      message={(
        <span className="excel-input-failure__title">
          表格输入不符合规范
          <Tag color="error">{failure.code}</Tag>
        </span>
      )}
      description={(
        <div className="excel-input-failure__body">
          <p>{failure.message}</p>
          <div className="excel-input-failure__action">
            <strong>需要人工处理</strong>
            <span>{failure.action}</span>
          </div>
          {failure.issues.length > 0 && (
            <ol className="excel-input-failure__issues" aria-label="需要修正的位置">
              {failure.issues.map((issue, index) => (
                <li key={`${issueLocation(issue)}-${index}`}>
                  <strong>{issueLocation(issue)}</strong>
                  {issue.value !== null && <span>原值：{issue.value}</span>}
                </li>
              ))}
            </ol>
          )}
          {failure.issues.length === 0 && failure.sheets.length > 0 && (
            <p className="excel-input-failure__sheets">
              涉及工作表：{failure.sheets.join('、')}
            </p>
          )}
          {requestId && (
            <small className="excel-input-failure__request">请求 {requestId}</small>
          )}
        </div>
      )}
    />
  );
}
