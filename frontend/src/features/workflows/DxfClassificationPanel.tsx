import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  FileSearchOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { executeWorkflowStage, getDxfClassification } from './workflows.api';
import { describeApiError } from '../../shared/api';
import type { DxfClassificationItem, WorkflowStage } from './workflow';

interface Props {
  workflowId: number;
  stage?: WorkflowStage;
  isCurrent: boolean;
  onChanged: () => void;
}

const DISPOSITION = {
  classified: { color: 'success', label: '已分类' },
  review_required: { color: 'warning', label: '待确认' },
  unreadable: { color: 'error', label: '无法读取' },
} as const;

export function DxfClassificationPanel({ workflowId, stage, isCurrent, onChanged }: Props) {
  const queryClient = useQueryClient();
  const runQ = useQuery({
    queryKey: ['workflow-dxf-classification', workflowId],
    queryFn: () => getDxfClassification(workflowId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'running' || stage?.status === 'queued' || stage?.status === 'running' ? 2000 : false;
    },
  });
  const executeMutation = useMutation({
    mutationFn: () => executeWorkflowStage(workflowId, 'dxf_classification', {
      execution_kind: 'steel_dxf_classification',
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['workflow-dxf-classification', workflowId] });
      onChanged();
    },
  });
  const run = runQ.data;
  const canExecute = isCurrent && ['waiting_input', 'ready', 'failed'].includes(stage?.status ?? '');
  const active = ['queued', 'running'].includes(stage?.status ?? '') || run?.status === 'running';

  const columns = [
    {
      title: '来源 DXF',
      dataIndex: 'source_name',
      render: (value: string) => (
        <Typography.Text className="workflow-classification-file-name">
          {value}
        </Typography.Text>
      ),
    },
    {
      title: '分流结果',
      render: (_: unknown, item: DxfClassificationItem) => {
        const meta = DISPOSITION[item.disposition as keyof typeof DISPOSITION]
          ?? { color: 'default', label: item.disposition || '未知处置' };
        return (
          <Space orientation="vertical" size={2}>
            <Space wrap><Tag color={meta.color}>{meta.label}</Tag>{item.part_type && <Tag color="blue">{item.part_type}</Tag>}</Space>
            <Typography.Text className="workflow-classification-file-name">
              {item.output_directory}
            </Typography.Text>
            <Typography.Text type="secondary" className="workflow-classification-file-name">
              {item.output_name}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: '诊断',
      dataIndex: 'diagnostics',
      render: (values: string[]) => values.length ? <Space wrap>{values.map((value) => <Tag key={value}>{value}</Tag>)}</Space> : '—',
    },
  ];

  return (
    <Card title={<Space><FileSearchOutlined />02 · DXF 预处理与分类分流</Space>} style={{ marginTop: 12 }}>
      {!isCurrent && !run && (
        <Alert type="info" showIcon message="等待输入冻结" description="服务器完成 DWG→DXF 和输入冻结后，才会开放分类分流。" />
      )}
      {isCurrent && !run && !active && (
        <Alert
          type="info"
          showIcon
          message="冻结 DXF 已就绪"
          description="系统将自动添加“_拆板前”后缀，按标题栏截面字段分流；不需要选择文件或填写路径。"
          action={<Button type="primary" icon={<ThunderboltOutlined />} loading={executeMutation.isPending} onClick={() => executeMutation.mutate()}>开始 DXF 分类分流</Button>}
        />
      )}
      {active && (
        <Alert
          type="info"
          showIcon
          message={`分类任务${stage?.job_id ? ` #${stage.job_id}` : ''}正在执行`}
          description={<Progress percent={stage?.progress ?? run?.job.progress ?? 0} status="active" />}
          action={<Button icon={<ReloadOutlined />} loading={runQ.isFetching} onClick={() => runQ.refetch()}>刷新</Button>}
        />
      )}
      {run?.status === 'failed' && (
        <Alert
          type="error"
          showIcon
          message={`${run.error_code ?? 'DXF_CLASSIFICATION_FAILED'} · 分类失败`}
          description={run.error_message ?? '请检查诊断后重试；旧 attempt 不会覆盖新结果。'}
          action={canExecute && <Button type="primary" danger loading={executeMutation.isPending} onClick={() => executeMutation.mutate()}>重试分类</Button>}
        />
      )}
      {executeMutation.error && (
        <Alert style={{ marginTop: 12 }} type="error" showIcon message="提交分类任务失败" description={describeApiError(executeMutation.error, '请稍后重试')} />
      )}
      {run && ['completed', 'completed_with_review'].includes(run.status) && (
        <>
          <Alert
            type={run.status === 'completed_with_review' ? 'warning' : 'success'}
            showIcon
            message={run.status === 'completed_with_review' ? '分类完成，存在待确认或无法读取图纸' : '全部 DXF 已完成分类分流'}
            description={`Steel DXF Classifier ${run.classifier_version} · 输入清单 ${run.input_manifest_sha256}`}
          />
          <div className="workflow-classification-summary">
            {[
              ['输入图纸', run.input_count],
              ['已分类', run.classified_count],
              ['待确认', run.review_required_count],
              ['无法读取', run.unreadable_count],
            ].map(([label, value]) => (
              <div key={label}>
                <small>{label}</small>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          <Space wrap style={{ marginTop: 14 }}>
            {Object.entries(run.type_counts).map(([type, count]) => <Tag color="blue" key={type}>{type} · {count}</Tag>)}
            {run.report_file && <Tag>分类报告已纳入生产压缩包</Tag>}
            {run.manifest_file && <Tag>分类清单已纳入生产压缩包</Tag>}
          </Space>
          <Collapse
            className="workflow-classification-details"
            items={[{
              key: 'files',
              label: `查看文件明细（${run.items.length}）`,
              children: (
                <Table<DxfClassificationItem>
                  rowKey="id"
                  dataSource={run.items}
                  columns={columns}
                  pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  scroll={{ x: 720 }}
                  locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无逐图结果" /> }}
                />
              ),
            }]}
          />
        </>
      )}
    </Card>
  );
}
