import { Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { listProjects } from '../../api/projects.api';

export function ProjectsPage() {
  const { data = [] } = useQuery({ queryKey: ['projects'], queryFn: listProjects });
  return (
    <>
      <Typography.Title level={3}>项目列表</Typography.Title>
      <Table rowKey="id" dataSource={data} columns={[
        { title: 'ID', dataIndex: 'id' },
        { title: '编号', dataIndex: 'code' },
        { title: '名称', dataIndex: 'name' },
        { title: '状态', dataIndex: 'status' },
      ]} />
    </>
  );
}
