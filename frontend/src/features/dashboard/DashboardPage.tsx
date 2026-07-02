import { Card, Space, Typography } from 'antd';
import { useAuthStore } from '../../stores/auth.store';

export function DashboardPage() {
  const user = useAuthStore((s) => s.user);
  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={3}>工作台</Typography.Title>
      <Card>
        当前用户：{user?.real_name}（{user?.username}）
      </Card>
      <Card title="当前阶段">
        本机开发骨架已保留 Agent、DXF、CAD Worker 边界，但暂不实现内部处理。
      </Card>
    </Space>
  );
}
