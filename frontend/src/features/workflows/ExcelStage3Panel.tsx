import { FileSearchOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Space, Tag, Typography } from 'antd';

import { parseApiError } from '../../shared/api';
import { ApiErrorAlert, ExcelInputFailurePanel } from '../../shared/components';
import type { WorkflowStage } from './workflow';
import { getWorkflowExcelStage3Preflight } from './workflows.api';

export function ExcelStage3Panel({
  workflowId,
  stage,
  isCurrent,
  executing,
  onExecute,
}: {
  workflowId: number;
  stage: WorkflowStage;
  isCurrent: boolean;
  executing: boolean;
  onExecute: () => void;
}) {
  const active = isCurrent && ['queued', 'running'].includes(stage.status);
  const preflightQ = useQuery({
    queryKey: ['workflow-excel-stage3-preflight', workflowId],
    queryFn: () => getWorkflowExcelStage3Preflight(workflowId),
    enabled: isCurrent && !active,
    retry: false,
  });
  const preflightError = preflightQ.isError
    ? parseApiError(preflightQ.error, 'Excel 第三阶段运行前检查失败')
    : null;
  const preflight = preflightQ.data;

  return (
    <Card className="workflow-excel-stage-card workflow-excel-stage3-card">
      <div className="workflow-excel-stage-source">
        <span className="workflow-excel-stage-icon" aria-hidden="true">
          <FileSearchOutlined />
        </span>
        <div>
          <Typography.Text strong>对接拆板后 DXF 进行异孔折判断图形分类</Typography.Text>
          <p>
            读取第二阶段正式 Excel 和拆板后 DXF，逐板进行外形、孔洞、折弯的图形分类并回填 part 表。
          </p>
        </div>
      </div>
      <ol className="workflow-excel-stage-route" aria-label="Excel 第三阶段处理路径">
        <li>第二阶段正式 Excel</li>
        <li>拆板结果 DXF 目录</li>
        <li>异孔折分类回填 part 表图形列</li>
      </ol>
      <Descriptions
        size="small"
        column={1}
        items={[
          { key: 'source', label: '读取依据', children: '第二阶段正式 Excel + 拆板后 processed_dxf，仅处理有拆板结果的零件' },
          { key: 'match', label: '匹配方式', children: '以零件号匹配 Stage 2 Excel 零件与 DXF 文件；无匹配 DXF 的零件图形列留空' },
          { key: 'outputs', label: '交付结果', children: '异孔折分类结果表与深化 Stage 2 Excel（含图形列）分别下载' },
        ]}
      />
      {active && (
        <Alert
          type="info"
          showIcon
          message={stage.status === 'queued'
            ? '异孔折判断任务已进入处理队列'
            : '服务器正在执行异孔折判断图形分类'}
          description={stage.status === 'queued'
            ? '系统会按队列顺序开始处理，页面将自动更新；无需重复点击。'
            : `当前进度 ${stage.progress}%，页面将自动刷新；处理完成后会开放 Excel 下载。`}
        />
      )}
      {isCurrent && !active && preflightQ.isLoading && (
        <Alert
          type="info"
          showIcon
          message="正在核验第三阶段输入"
          description="正在核对第二阶段正式结果和拆板后 DXF 产物。"
        />
      )}
      {isCurrent && !active && preflight?.ready && (
        <Alert
          type="success"
          showIcon
          message="第三阶段输入核验通过"
          description={(
            <Space orientation="vertical" size={4}>
              <Typography.Text>第二阶段结果：{preflight.stage2_file_name}</Typography.Text>
              <Typography.Text>
                已冻结 {preflight.dxf_count} 张拆板后 DXF
              </Typography.Text>
              <Space wrap size={[4, 4]}>
                {preflight.checks.map((check) => (
                  <Tag color="green" key={check.code}>{check.label}</Tag>
                ))}
              </Space>
            </Space>
          )}
        />
      )}
      {isCurrent && !active && preflightError?.failure && (
        <ExcelInputFailurePanel
          failure={preflightError.failure}
          requestId={preflightError.requestId}
        />
      )}
      {isCurrent && !active && preflightError && !preflightError.failure && (
        <ApiErrorAlert
          title="第三阶段运行前检查未通过"
          error={preflightQ.error}
          fallback="Excel 第三阶段运行前检查未通过"
          retryLabel="重新检查"
          retryLoading={preflightQ.isFetching}
          onRetry={() => preflightQ.refetch()}
        />
      )}
      {isCurrent && (
        <Button
          type="primary"
          size="large"
          icon={<ThunderboltOutlined />}
          loading={executing || active || preflightQ.isLoading}
          disabled={active || !preflight?.ready}
          onClick={onExecute}
        >
          {active ? '正在执行异孔折判断' : '运行异孔折判断'}
        </Button>
      )}
    </Card>
  );
}
