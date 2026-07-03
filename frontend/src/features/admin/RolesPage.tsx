import { Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { listPermissions, listRoles } from '../../api/roles.api';

export function RolesPage() {
  const roles = useQuery({ queryKey: ['roles'], queryFn: listRoles });
  const permissions = useQuery({ queryKey: ['permissions'], queryFn: listPermissions });

  return (
    <>
      <Typography.Title level={3}>角色权限</Typography.Title>
      <Typography.Title level={4}>角色</Typography.Title>
      <Table
        rowKey="id"
        dataSource={roles.data ?? []}
        pagination={false}
        columns={[
          { title: 'ID', dataIndex: 'id' },
          { title: '编码', dataIndex: 'code' },
          { title: '名称', dataIndex: 'name' },
          { title: '描述', dataIndex: 'description' },
        ]}
      />
      <Typography.Title level={4} style={{ marginTop: 24 }}>权限</Typography.Title>
      <Table
        rowKey="id"
        dataSource={permissions.data ?? []}
        pagination={false}
        columns={[
          { title: 'ID', dataIndex: 'id' },
          { title: '编码', dataIndex: 'code' },
          { title: '资源', dataIndex: 'resource' },
          { title: '动作', dataIndex: 'action' },
          { title: '名称', dataIndex: 'name' },
        ]}
      />
    </>
  );
}
