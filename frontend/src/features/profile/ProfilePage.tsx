import { Card, Descriptions, Space, Tag, Typography } from 'antd';
import { useAuthStore } from '../../stores/auth.store';

export function ProfilePage() {
  const user = useAuthStore((state) => state.user);

  return (
    <>
      <Typography.Title level={3}>个人中心</Typography.Title>
      <Card>
        <Descriptions column={1} bordered>
          <Descriptions.Item label="账号">{user?.username}</Descriptions.Item>
          <Descriptions.Item label="姓名">{user?.real_name}</Descriptions.Item>
          <Descriptions.Item label="邮箱">{user?.email || '-'}</Descriptions.Item>
          <Descriptions.Item label="状态">{user?.status}</Descriptions.Item>
          <Descriptions.Item label="角色">
            <Space wrap>
              {user?.roles.map((role) => (
                <Tag key={role.code}>{role.name || role.code}</Tag>
              ))}
            </Space>
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </>
  );
}
