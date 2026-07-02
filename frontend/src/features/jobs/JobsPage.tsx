import { Button, Table, Typography, message } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { createFrameworkSmokeJob, listJobs } from '../../api/jobs.api';

export function JobsPage() {
  const query = useQuery({ queryKey: ['jobs'], queryFn: listJobs, refetchInterval: 3000 });
  async function createJob() {
    await createFrameworkSmokeJob();
    message.success('已创建框架冒烟任务');
    query.refetch();
  }
  return (
    <>
      <Typography.Title level={3}>任务列表</Typography.Title>
      <Button type="primary" onClick={createJob}>创建框架冒烟任务</Button>
      <Table style={{ marginTop: 16 }} rowKey="id" dataSource={query.data ?? []} columns={[
        { title: 'ID', dataIndex: 'id' },
        { title: '任务类型', dataIndex: 'task_type' },
        { title: '管线', dataIndex: 'pipeline' },
        { title: '状态', dataIndex: 'status' },
        { title: '进度', dataIndex: 'progress' },
      ]} />
    </>
  );
}
