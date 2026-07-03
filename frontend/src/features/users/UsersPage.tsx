import { Space, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { listUsers } from '../../api/users.api';

export function UsersPage() {
  const { data = [] } = useQuery({ queryKey: ['users'], queryFn: listUsers });
  return (
    <>
      <Typography.Title level={3}>用户管理</Typography.Title>
      <Table
        rowKey="id"
        dataSource={data}
        columns={[
          { title: 'ID', dataIndex: 'id' },
          { title: '账号', dataIndex: 'username' },
          { title: '姓名', dataIndex: 'real_name' },
          { title: '邮箱', dataIndex: 'email' },
          { title: '状态', dataIndex: 'status' },
          {
            title: '角色',
            dataIndex: 'roles',
            render: (_, record) => (
              <Space wrap>
                {record.roles.map((role) => (
                  <Tag key={role.code}>{role.name || role.code}</Tag>
                ))}
              </Space>
            ),
          },
        ]}
      />
    </>
  );
}
