import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { AppProviders } from './app/providers';
import { AppRouter } from './app/router';

export function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <AppProviders>
        <AppRouter />
      </AppProviders>
    </ConfigProvider>
  );
}
