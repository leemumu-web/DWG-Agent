import { Button, Card, Form, InputNumber, Select, Space, Switch, Typography } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import type { RemnantMaterial, RemnantSearch, RemnantStatus } from './types';

interface Props {
  materials: RemnantMaterial[];
  loading: boolean;
  value: RemnantSearch;
  onSearch: (value: RemnantSearch) => void;
}

const statusOptions: { label: string; value: RemnantStatus }[] = [
  { label: '可用', value: 'available' },
  { label: '已预占', value: 'reserved' },
  { label: '已使用（历史）', value: 'used' },
  { label: '已归档（历史）', value: 'archived' },
];

export function RemnantSearchPanel({ materials, loading, value, onSearch }: Props) {
  const [form] = Form.useForm();
  return (
    <Card className="remnant-search-card" bordered={false}>
      <div className="remnant-section-heading">
        <div>
          <Typography.Title level={4}>按规格查找余料</Typography.Title>
          <Typography.Text type="secondary">材质和厚度为必填条件，可按同系列牌号扩展。</Typography.Text>
        </div>
      </div>
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          materialId: value.materialId,
          thicknessMm: value.thicknessMm ? Number(value.thicknessMm) : undefined,
          includeFamily: value.includeFamily,
          statuses: value.statuses,
        }}
        onFinish={(fields) => onSearch({
          materialId: fields.materialId,
          thicknessMm: String(fields.thicknessMm),
          includeFamily: Boolean(fields.includeFamily),
          statuses: fields.statuses,
          page: 1,
        })}
      >
        <div className="remnant-search-grid">
          <Form.Item name="materialId" label="标准材质" rules={[{ required: true, message: '请选择材质' }]}>
            <Select
              showSearch
              loading={loading}
              placeholder="例如 Q235B"
              optionFilterProp="label"
              options={materials.map((item) => ({ value: item.id, label: item.code }))}
            />
          </Form.Item>
          <Form.Item name="thicknessMm" label="厚度（mm）" rules={[{ required: true, message: '请输入厚度' }]}>
            <InputNumber min={0.001} precision={3} step={0.5} placeholder="10.000" style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="statuses" label="库存状态">
            <Select mode="multiple" options={statusOptions} maxTagCount="responsive" />
          </Form.Item>
          <Form.Item name="includeFamily" label="查询范围" valuePropName="checked">
            <Switch checkedChildren="包含同系列" unCheckedChildren="仅此材质" />
          </Form.Item>
        </div>
        <Space>
          <Button type="primary" htmlType="submit" icon={<SearchOutlined />}>查询余料</Button>
          <Typography.Text type="secondary">默认只显示可用和已预占记录</Typography.Text>
        </Space>
      </Form>
    </Card>
  );
}

