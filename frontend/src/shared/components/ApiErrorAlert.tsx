import { ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Space, Typography } from 'antd';
import type { ReactNode } from 'react';
import { apiErrorRecovery, parseApiError } from '../api';

export function ApiErrorAlert({
  title,
  error,
  fallback,
  onRetry,
  retryLabel = '重试',
  retryLoading = false,
  extraAction,
}: {
  title: string;
  error: unknown;
  fallback: string;
  onRetry?: () => unknown;
  retryLabel?: string;
  retryLoading?: boolean;
  extraAction?: ReactNode;
}) {
  const parsed = parseApiError(error, fallback);
  const canRetry = Boolean(onRetry && parsed.retryable);
  return (
    <Alert
      className="operator-error-alert"
      type="error"
      showIcon
      message={title}
      description={(
        <Space orientation="vertical" size={4}>
          <Typography.Text>{parsed.message}</Typography.Text>
          <Typography.Text>
            <Typography.Text strong>处理建议：</Typography.Text>
            {apiErrorRecovery(parsed)}
          </Typography.Text>
        </Space>
      )}
      action={canRetry || extraAction ? (
        <Space wrap>
          {canRetry && onRetry && (
            <Button
              icon={<ReloadOutlined />}
              loading={retryLoading}
              onClick={() => { void onRetry(); }}
            >
              {retryLabel}
            </Button>
          )}
          {extraAction}
        </Space>
      ) : undefined}
    />
  );
}
