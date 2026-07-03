import { Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { listDrawings } from '../../api/drawings.api';

export function DrawingsPage() {
  const { data = [] } = useQuery({ queryKey: ['drawings'], queryFn: listDrawings });
  return (
    <>
      <Typography.Title level={3}>图纸管理</Typography.Title>
      <Table
        rowKey="id"
        dataSource={data}
        columns={[
          { title: 'ID', dataIndex: 'id' },
          { title: '项目', dataIndex: 'project_id' },
          { title: '图号', dataIndex: 'drawing_no' },
          { title: '标题', dataIndex: 'title' },
          { title: '专业', dataIndex: 'discipline' },
          { title: '状态', dataIndex: 'status' },
        ]}
      />
    </>
  );
}
