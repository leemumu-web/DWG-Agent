import { Card, Tag, Typography } from 'antd';
import { ClockCircleOutlined } from '@ant-design/icons';

export function FutureStageNotice() {
  return (
    <Card className="workflow-future-stage">
      <div className="workflow-future-stage__icon" aria-hidden="true">
        <ClockCircleOutlined />
      </div>
      <div>
        <div className="workflow-future-stage__title">
          <Typography.Text strong>能力等待上线</Typography.Text>
          <Tag>等待上线</Tag>
        </div>
        <Typography.Text type="secondary">
          流程位置与数据接口已经预留，执行能力将在后续版本接入。
        </Typography.Text>
      </div>
    </Card>
  );
}
