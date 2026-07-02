import { Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { listFiles } from '../../api/files.api';
import { FileUpload } from '../../components/FileUpload';

export function FilesPage() {
  const query = useQuery({ queryKey: ['files'], queryFn: listFiles });
  return (
    <>
      <Typography.Title level={3}>文件管理</Typography.Title>
      <FileUpload onUploaded={() => query.refetch()} />
      <Table style={{ marginTop: 16 }} rowKey="id" dataSource={query.data ?? []} columns={[
        { title: 'ID', dataIndex: 'id' },
        { title: '文件名', dataIndex: 'original_name' },
        { title: '大小', dataIndex: 'size_bytes' },
        { title: '状态', dataIndex: 'status' },
      ]} />
    </>
  );
}
