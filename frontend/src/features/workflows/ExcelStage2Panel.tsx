import { FileSearchOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Space, Tag, Typography } from 'antd';

import { parseApiError } from '../../shared/api';
import { ApiErrorAlert, ExcelInputFailurePanel } from '../../shared/components';
import type { WorkflowStage } from './workflow';
import { getWorkflowExcelStage2Preflight } from './workflows.api';

export function ExcelStage2Panel({
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
    queryKey: ['workflow-excel-stage2-preflight', workflowId],
    queryFn: () => getWorkflowExcelStage2Preflight(workflowId),
    enabled: isCurrent && !active,
    retry: false,
  });
  const preflightError = preflightQ.isError
    ? parseApiError(preflightQ.error, 'Excel 第二阶段运行前检查失败')
    : null;
  const preflight = preflightQ.data;

  return (
    <Card className="workflow-excel-stage-card workflow-excel-stage2-card">
      <div className="workflow-excel-stage-source">
        <span className="workflow-excel-stage-icon" aria-hidden="true">
          <FileSearchOutlined />
        </span>
        <div>
          <Typography.Text strong>以当前项目的冻结 BH 图纸深化 Excel</Typography.Text>
          <p>
            只读取分类阶段已冻结的拆板前 BH DXF；先生成左右进读取表，再更新同一批次的整理表和 part 表。
          </p>
        </div>
      </div>
      <ol className="workflow-excel-stage-route" aria-label="Excel 第二阶段处理路径">
        <li>第一阶段正式 Excel</li>
        <li>BH 左右进读取表</li>
        <li>整理表与 part 表</li>
      </ol>
      <Descriptions
        size="small"
        column={1}
        items={[
          { key: 'source', label: '读取依据', children: '当前分类账中的拆板前 BH DXF，不读取拆板产物' },
          { key: 'match', label: '匹配方式', children: '以零件号匹配；翼板唯一时直接填写，多种翼板时按方案增行，并同步整理表与 part 表' },
          { key: 'outputs', label: '交付结果', children: 'BH 左右进读取表与深化后的单个 Excel 文件分别下载' },
        ]}
      />
      {active && (
        <Alert
          type="info"
          showIcon
          message={stage.status === 'queued'
            ? 'BH 左右进任务已进入处理队列'
            : '服务器正在处理 BH 左右进'}
          description={stage.status === 'queued'
            ? '系统会按队列顺序开始处理，页面将自动更新；无需重复点击。'
            : `当前进度 ${stage.progress}%，页面将自动刷新；处理完成后会开放两份 Excel 下载。`}
        />
      )}
      {isCurrent && !active && preflightQ.isLoading && (
        <Alert
          type="info"
          showIcon
          message="正在核验第二阶段输入"
          description="正在核对第一阶段正式结果、当前分类任务和冻结的 BH 图纸清单。"
        />
      )}
      {isCurrent && !active && preflight?.ready && (
        <Alert
          type="success"
          showIcon
          message="第二阶段输入核验通过"
          description={(
            <Space orientation="vertical" size={4}>
              <Typography.Text>第一阶段结果：{preflight.stage1_file_name}</Typography.Text>
              <Typography.Text>
                {preflight.mode === 'no_bh_inputs'
                  ? '当前项目没有 BH 图纸，第二阶段将保留第一阶段 Excel 原样输出'
                  : `已冻结 ${preflight.bh_input_count} 张拆板前 BH 图纸`}
              </Typography.Text>
              <Space wrap size={[4, 4]}>
                {preflight.checks
                  .filter((check) => check.code !== 'bh_batch_frozen')
                  .map((check) => (
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
          title="第二阶段运行前检查未通过"
          error={preflightQ.error}
          fallback="Excel 第二阶段运行前检查未通过"
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
          {active ? '正在处理 BH 的左右进' : '处理 BH 的左右进'}
        </Button>
      )}
    </Card>
  );
}
