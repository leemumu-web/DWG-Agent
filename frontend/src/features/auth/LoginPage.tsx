import { Button, Card, Form, Input, Typography, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import { login } from '../../api/auth.api';
import { useAuthStore } from '../../stores/auth.store';

export function LoginPage() {
  const navigate = useNavigate();
  const setSession = useAuthStore((s) => s.setSession);

  async function onFinish(values: { username: string; password: string }) {
    try {
      const data = await login(values.username, values.password);
      setSession(data.access_token, data.user);
      navigate('/dashboard');
    } catch {
      message.error('登录失败，请检查账号密码或后端服务');
    }
  }

  return (
    <div className="login-page">
      <Card className="login-card">
        <div className="login-brand">
          <img src="/logo.png" alt="DWG-Agent Logo" className="login-logo" />
          <Typography.Title level={3} style={{ margin: 0 }}>DWG-Agent 平台登录</Typography.Title>
        </div>
        <Form layout="vertical" onFinish={onFinish} initialValues={{ username: 'admin', password: 'admin123456' }}>
          <Form.Item name="username" label="账号" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password /></Form.Item>
          <Button type="primary" htmlType="submit" block>登录</Button>
        </Form>
      </Card>
    </div>
  );
}
