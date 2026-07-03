import { Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { listAuditLogs } from '../../api/audit-logs.api';

export function AuditLogsPage() {
  const { data = [] } = useQuery({ queryKey: ['audit-logs'], queryFn: listAuditLogs });
  return (
    <>
      <Typography.Title level={3}>审计日志</Typography.Title>
      <Table
        rowKey="id"
        dataSource={data}
        columns={[
          { title: 'ID', dataIndex: 'id' },
          { title: '操作人', dataIndex: 'actor_user_id' },
          { title: '动作', dataIndex: 'action' },
          { title: '资源类型', dataIndex: 'resource_type' },
          { title: '资源 ID', dataIndex: 'resource_id' },
          { title: '时间', dataIndex: 'created_at' },
        ]}
      />
    </>
  );
}
