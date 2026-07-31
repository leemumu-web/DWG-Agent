import { App, ConfigProvider, Spin } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PropsWithChildren } from 'react';
import { shouldRetryApiQuery } from '../shared/api';
import { AppErrorBoundary, ConnectivityBanner } from '../shared/components';
import { useAuthInit } from '../shared/auth';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: shouldRetryApiQuery,
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 10000),
    },
  },
});

export function AppProviders({ children }: PropsWithChildren) {
  const authReady = useAuthInit();

  if (!authReady) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" description="初始化中…" />
      </div>
    );
  }

  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: '#2563eb',
          colorInfo: '#2563eb',
          colorSuccess: '#059669',
          colorWarning: '#d97706',
          colorError: '#dc2626',
          colorText: '#172033',
          colorTextSecondary: '#667085',
          colorBgLayout: '#f4f7fb',
          colorBorderSecondary: '#e7ebf1',
          borderRadius: 10,
          borderRadiusLG: 14,
          fontFamily: 'Inter, "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif',
          boxShadowSecondary: '0 12px 32px rgba(15, 23, 42, 0.08)',
        },
        components: {
          Layout: { headerBg: 'rgba(255,255,255,.92)', siderBg: '#111827' },
          Menu: { darkItemBg: '#111827', darkItemSelectedBg: '#2563eb', darkItemHoverBg: '#1f2937' },
          Card: { headerFontSize: 15, bodyPadding: 20 },
          Table: { headerBg: '#f8fafc', headerColor: '#667085', cellPaddingBlock: 14, cellPaddingInline: 16 },
          Button: { controlHeight: 36, primaryShadow: '0 6px 16px rgba(37,99,235,.18)' },
          Input: { controlHeight: 36 },
          Drawer: { paddingLG: 24 },
          Segmented: { trackBg: '#edf2f7' },
          Progress: { remainingColor: '#e9eef5' },
        },
      }}
    >
      <QueryClientProvider client={queryClient}>
        <App>
          <ConnectivityBanner />
          <AppErrorBoundary>{children}</AppErrorBoundary>
        </App>
      </QueryClientProvider>
    </ConfigProvider>
  );
}
