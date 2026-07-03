import { Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { listPendingReviews } from '../../api/reviews.api';

export function ReviewsPage() {
  const { data = [] } = useQuery({ queryKey: ['reviews', 'pending'], queryFn: listPendingReviews });
  return (
    <>
      <Typography.Title level={3}>待复核列表</Typography.Title>
      <Table
        rowKey="id"
        dataSource={data}
        columns={[
          { title: '结果 ID', dataIndex: 'id' },
          { title: '任务 ID', dataIndex: 'job_id' },
          { title: '图纸 ID', dataIndex: 'drawing_id' },
          { title: '结果类型', dataIndex: 'result_type' },
          { title: '置信度', dataIndex: 'confidence' },
          { title: '状态', dataIndex: 'status' },
        ]}
      />
    </>
  );
}
