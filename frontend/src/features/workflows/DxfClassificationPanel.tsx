import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  DownloadOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { downloadFile } from '../files';
import { executeWorkflowStage, getDxfClassification } from './workflows.api';
import { describeApiError } from '../../shared/api';
import { fmtSize } from '../../shared/components';
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
      render: (value: string, item: DxfClassificationItem) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text>{value}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>文件 #{item.source_file.id}</Typography.Text>
        </Space>
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
            <Typography.Text>{item.output_directory}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>{item.output_name} · {fmtSize(item.output_file.size_bytes)}</Typography.Text>
          </Space>
        );
      },
    },
    {
      title: '诊断',
      dataIndex: 'diagnostics',
      render: (values: string[]) => values.length ? <Space wrap>{values.map((value) => <Tag key={value}>{value}</Tag>)}</Space> : '—',
    },
    {
      title: '操作',
      width: 92,
      render: (_: unknown, item: DxfClassificationItem) => (
        <Button type="link" icon={<DownloadOutlined />} onClick={() => downloadFile(item.output_file.id, item.output_name)}>下载</Button>
      ),
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
          <Descriptions
            bordered
            size="small"
            column={4}
            style={{ marginTop: 16 }}
            items={[
              { key: 'input', label: '输入', children: run.input_count },
              { key: 'classified', label: '已分类', children: run.classified_count },
              { key: 'review', label: '待确认', children: run.review_required_count },
              { key: 'unreadable', label: '无法读取', children: run.unreadable_count },
            ]}
          />
          <Space wrap style={{ marginTop: 14 }}>
            {Object.entries(run.type_counts).map(([type, count]) => <Tag color="blue" key={type}>{type} · {count}</Tag>)}
            {run.report_file && <Button icon={<DownloadOutlined />} onClick={() => downloadFile(run.report_file!.id, run.report_file!.original_name)}>分类报告 JSON</Button>}
            {run.manifest_file && <Button icon={<DownloadOutlined />} onClick={() => downloadFile(run.manifest_file!.id, run.manifest_file!.original_name)}>分类清单 CSV</Button>}
          </Space>
          <Table<DxfClassificationItem>
            rowKey="id"
            dataSource={run.items}
            columns={columns}
            pagination={false}
            scroll={{ x: 780 }}
            style={{ marginTop: 16 }}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无逐图结果" /> }}
          />
        </>
      )}
    </Card>
  );
}
