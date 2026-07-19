import { useEffect, useState } from 'react';
import { Alert } from 'antd';

/** Inform without discarding page state when the browser network changes. */
export function ConnectivityBanner() {
  const [online, setOnline] = useState(() => navigator.onLine);

  useEffect(() => {
    const onOnline = () => setOnline(true);
    const onOffline = () => setOnline(false);
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);
    return () => {
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
    };
  }, []);

  if (online) return null;
  return <Alert className="connectivity-banner" type="warning" banner showIcon message="网络连接已中断：当前页面数据会保留，恢复连接后可使用“刷新”重新同步。" />;
}
