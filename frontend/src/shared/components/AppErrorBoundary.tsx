import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button, Result } from 'antd';

interface Props { children: ReactNode; }
interface State { failed: boolean; }

/** Last-resort route safety net; request errors remain handled by React Query. */
export class AppErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError() { return { failed: true }; }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled frontend rendering error', error, info);
  }

  render() {
    if (this.state.failed) {
      return <Result
        status="error"
        title="页面未能安全显示"
        subTitle="已保护当前登录状态。请重新加载；若问题持续出现，请记录 Request ID 后联系管理员。"
        extra={<Button type="primary" onClick={() => window.location.reload()}>重新加载当前页面</Button>}
      />;
    }
    return this.props.children;
  }
}
