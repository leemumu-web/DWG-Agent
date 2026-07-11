import { Empty, Steps, Tag, Typography } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import type { JobStep } from '../types/job';

const stepLabels: Record<string, string> = {
  download_source_dwg: '下载源 DWG',
  run_oda_convert: 'ODA 转换 DWG→DXF',
  persist_dxf_result: '持久化 DXF 结果',
};

function stepStatusIcon(step: JobStep) {
  if (step.status === 'succeeded') return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
  if (step.status === 'failed') return <CloseCircleOutlined style={{ color: '#ff4d4f' }} />;
  return <LoadingOutlined style={{ color: '#1677ff' }} />;
}

function stepLabel(step: JobStep) {
  const key = Object.keys(stepLabels).find((k) => step.step_name.startsWith(k));
  return stepLabels[key ?? step.step_name] ?? step.step_name;
}

export function JobTimeline({ steps }: { steps: JobStep[] }) {
  if (!steps || steps.length === 0) {
    return <Empty description="暂无步骤记录" />;
  }

  const items = steps.map((step) => ({
    title: stepLabel(step),
    description: (
      <div>
        <Tag bordered={false}>第 {step.attempt} 次</Tag>
        {step.error_message && (
          <Typography.Text type="danger">{step.error_message}</Typography.Text>
        )}
        {step.worker_name && (
          <div>
            <Typography.Text type="secondary">
              worker: {step.worker_name}
            </Typography.Text>
          </div>
        )}
        {step.output_json && (
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {JSON.stringify(step.output_json).slice(0, 120)}
            </Typography.Text>
          </div>
        )}
      </div>
    ),
    status: (step.status === 'succeeded' ? 'finish' : step.status === 'failed' ? 'error' : 'process') as 'finish' | 'error' | 'process',
    icon: stepStatusIcon(step),
  }));

  return <Steps direction="vertical" size="small" current={steps.length} items={items} />;
}
