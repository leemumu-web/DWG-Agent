import { useState } from 'react';
import { Button, Card, Form, Input, Space, Table, Tag, message, Typography } from 'antd';
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import api from '../../shared/api';

const { Title, Text } = Typography;

interface PartItem {
  id: number;
  part_name: string;
  dxf_file: string;
  category: string;
  shape: string;
  hole: string;
  bend: string;
}

interface ClassificationRun {
  id: number;
  status: string;
  project_name: string;
  input_directory: string;
  input_count: number;
  classified_count: number;
  category_counts: Record<string, number> | null;
  items: PartItem[];
  started_at: string | null;
  finished_at: string | null;
}

const CATEGORY_COLORS: Record<string, string> = {
  '方': 'green',
  '异': 'orange',
  '方孔': 'cyan',
  '异孔': 'blue',
  '方折': 'lime',
  '异折': 'gold',
  '方孔折': 'geekblue',
  '异孔折': 'purple',
};

export function PlateClassificationPage() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [runs, setRuns] = useState<ClassificationRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<ClassificationRun | null>(null);

  const triggerClassification = async (values: { input_directory: string; project_name: string }) => {
    setLoading(true);
    try {
      const resp = await api.post('/plate-classification/runs', {
        workflow_run_id: 0,
        project_id: 0,
        ...values,
      });
      message.success(`任务已提交，run_id=${resp.data.run_id}`);
      loadRuns();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败');
    } finally {
      setLoading(false);
    }
  };

  const loadRuns = async () => {
    try {
      const resp = await api.get('/plate-classification/runs');
      setRuns(resp.data.items);
    } catch (e: any) {
      message.error('加载运行列表失败');
    }
  };

  const loadRunDetail = async (runId: number) => {
    try {
      const resp = await api.get(`/plate-classification/runs/${runId}`);
      setSelectedRun(resp.data);
    } catch (e: any) {
      message.error('加载详情失败');
    }
  };

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '项目', dataIndex: 'project_name' },
    { title: '状态', dataIndex: 'status', render: (s: string) => (
      <Tag color={s === 'completed' ? 'green' : s === 'failed' ? 'red' : 'blue'}>{s}</Tag>
    )},
    { title: 'DXF 数', dataIndex: 'input_count' },
    { title: '板件数', dataIndex: 'classified_count' },
    {
      title: '操作',
      render: (_: any, record: ClassificationRun) => (
        <Button size="small" onClick={() => loadRunDetail(record.id)}>详情</Button>
      ),
    },
  ];

  const partColumns = [
    { title: '板件名称', dataIndex: 'part_name' },
    { title: 'DXF 文件', dataIndex: 'dxf_file' },
    { title: '类别', dataIndex: 'category', render: (c: string) => (
      <Tag color={CATEGORY_COLORS[c] || 'default'}>{c}</Tag>
    )},
    { title: '外形', dataIndex: 'shape' },
    { title: '孔洞', dataIndex: 'hole' },
    { title: '折弯', dataIndex: 'bend' },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Title level={3}>板件分类（异孔折判断）</Title>

      <Card title="触发分类" style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" onFinish={triggerClassification}>
          <Form.Item name="input_directory" label="DXF 目录" rules={[{ required: true }]}>
            <Input placeholder="/path/to/dxf/dir" style={{ width: 300 }} />
          </Form.Item>
          <Form.Item name="project_name" label="项目名" rules={[{ required: true }]}>
            <Input placeholder="项目名称" style={{ width: 200 }} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" icon={<PlayCircleOutlined />} loading={loading}>
                开始分类
              </Button>
              <Button icon={<ReloadOutlined />} onClick={loadRuns}>刷新列表</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>

      <Card title="分类运行列表">
        <Table
          dataSource={runs}
          columns={columns}
          rowKey="id"
          pagination={{ pageSize: 10 }}
        />
      </Card>

      {selectedRun && (
        <Card title={`运行详情 #${selectedRun.id}`} style={{ marginTop: 16 }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text>状态: {selectedRun.status} | 输入: {selectedRun.input_directory}</Text>
            {selectedRun.category_counts && (
              <Space>
                {Object.entries(selectedRun.category_counts).map(([k, v]) => (
                  <Tag key={k} color={CATEGORY_COLORS[k] || 'default'}>{k}: {v}</Tag>
                ))}
              </Space>
            )}
            <Table
              dataSource={selectedRun.items}
              columns={partColumns}
              rowKey="id"
              size="small"
              pagination={{ pageSize: 20 }}
            />
          </Space>
        </Card>
      )}
    </div>
  );
}
