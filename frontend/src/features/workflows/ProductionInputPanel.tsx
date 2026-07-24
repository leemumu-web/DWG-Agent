import { useRef } from 'react';
import {
  CheckCircleOutlined,
  CloudUploadOutlined,
  FileExcelOutlined,
  FileOutlined,
  LoadingOutlined,
  LockOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Popconfirm,
  Progress,
  Row,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
} from 'antd';
import { describeApiError } from '../../shared/api';
import {
  clearWorkflowInputFolder,
  createWorkflowInputBatch,
  freezeWorkflowInputBatch,
  getWorkflowInputBatch,
  requestWorkflowInputConversions,
  uploadWorkflowInputFolder,
} from './workflow-inputs.api';
import { fmtDateTime, fmtSize } from '../../shared/components';
import type { WorkflowInputBatch, WorkflowInputItem } from './workflow-input';

const ACTIVE_BATCH = new Set(['converting']);
const ACTIVE_JOB = new Set(['queued', 'running', 'retrying']);

function itemStatus(item: WorkflowInputItem) {
  if (item.status === 'paired') return <Tag color="success">已配对</Tag>;
  if (item.status === 'failed') return <Tag color="error">需修复</Tag>;
  if (ACTIVE_JOB.has(item.conversion_job?.status ?? '')) return <Tag icon={<LoadingOutlined />} color="processing">转换中</Tag>;
  if (item.role === 'source_excel') return <Tag color="blue">已校验</Tag>;
  return <Tag>待转换</Tag>;
}

export function ProductionInputPanel({
  workflowId,
  sourceIntakeActive,
  onFrozen,
}: {
  workflowId: number;
  sourceIntakeActive: boolean;
  onFrozen: () => void;
}) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const folderInput = useRef<HTMLInputElement>(null);

  const batchQ = useQuery({
    queryKey: ['workflow-input-batch', workflowId],
    queryFn: () => sourceIntakeActive
      ? createWorkflowInputBatch(workflowId)
      : getWorkflowInputBatch(workflowId),
    refetchInterval: (query) => ACTIVE_BATCH.has(query.state.data?.status ?? '') ? 2500 : false,
  });
  const batch = batchQ.data;
  const editable = Boolean(sourceIntakeActive && batch?.status !== 'frozen');
  const refresh = (next?: WorkflowInputBatch) => {
    if (next) queryClient.setQueryData(['workflow-input-batch', workflowId], next);
    else void batchQ.refetch();
  };

  const clearM = useMutation({
    mutationFn: () => clearWorkflowInputFolder(workflowId),
    onSuccess: () => { message.success('生产输入文件夹已清空，可重新选择完整文件夹'); refresh(); },
    onError: (error) => message.error(describeApiError(error, '清空失败')),
  });
  const convertM = useMutation({
    mutationFn: () => requestWorkflowInputConversions(workflowId),
    onSuccess: (result) => {
      refresh(result.batch);
      message.success(result.dispatched_count > 0 ? `已提交 ${result.dispatched_count} 个 DWG 转换任务` : '没有重复投递，继续跟踪现有任务');
    },
    onError: (error) => message.error(describeApiError(error, '转换提交失败')),
  });
  const freezeM = useMutation({
    mutationFn: () => freezeWorkflowInputBatch(workflowId),
    onSuccess: (result) => { refresh(result); message.success('输入已冻结，生产流程进入下一阶段'); onFrozen(); },
    onError: (error) => { message.error(describeApiError(error, '冻结失败')); refresh(); },
  });
  const uploadFolderM = useMutation({
    mutationFn: (files: File[]) => uploadWorkflowInputFolder(workflowId, files),
    onSuccess: (result) => {
      refresh(result);
      message.success(`完整文件夹已上传并校验，共 ${result.items.length} 个文件`);
    },
    onError: (error) => message.error(describeApiError(error, '文件夹上传失败')),
  });

  const handleFolder = (selected: File[]) => {
    if (!batch || !editable) return;
    const invalid = selected.find((file) => !/\.(dwg|xls|xlsx)$/i.test(file.name));
    if (invalid) {
      message.error(`${invalid.name} 格式不允许；文件夹只能包含 DWG 和一个 Excel`);
      return;
    }
    const dwgCount = selected.filter((file) => /\.dwg$/i.test(file.name)).length;
    const excelCount = selected.filter((file) => /\.xlsx?$/i.test(file.name)).length;
    const roots = new Set(selected.map((file) => file.webkitRelativePath.split('/')[0]));
    if (!selected.length || dwgCount < 1 || excelCount !== 1 || roots.size !== 1
      || selected.some((file) => !file.webkitRelativePath.includes('/'))) {
      message.error('请选择一个完整文件夹：至少一个 DWG、恰好一个 Excel，且不能混入其他格式');
      return;
    }
    uploadFolderM.mutate(selected);
  };

  if (batchQ.isError) {
    return <Alert type="error" showIcon message="生产输入批次加载失败" description={describeApiError(batchQ.error, '请刷新后重试')} action={<Button onClick={() => batchQ.refetch()}>重试</Button>} />;
  }

  const columns = [
    {
      title: '源文件', key: 'file',
      render: (_: unknown, item: WorkflowInputItem) => <Space><FileOutlined /><div><Typography.Text strong>{item.original_name}</Typography.Text><div><Typography.Text type="secondary" style={{ fontSize: 12 }}>#{item.file.id} · {fmtSize(item.file.size_bytes)} · SHA-256 {item.file.sha256.slice(0, 10)}…</Typography.Text></div></div></Space>,
    },
    { title: '类型', dataIndex: 'role', width: 86, render: (role: string) => role === 'source_excel' ? <Tag icon={<FileExcelOutlined />} color="green">Excel</Tag> : <Tag color="blue">DWG</Tag> },
    {
      title: '服务器处理', key: 'processing', width: 210,
      render: (_: unknown, item: WorkflowInputItem) => <div>{itemStatus(item)}{item.conversion_job && <><Progress percent={item.conversion_job.progress ?? 0} size="small" /><Typography.Text type="secondary" style={{ fontSize: 12 }}>任务 #{item.conversion_job.id} · 尝试 #{item.conversion_job.attempt}</Typography.Text></>}{item.derived_dxf && <div><Typography.Text type="secondary">{item.derived_dxf.original_name} 已纳入生产压缩包</Typography.Text></div>}</div>,
    },
    {
      title: '反馈', key: 'feedback',
      render: (_: unknown, item: WorkflowInputItem) => item.error_code
        ? <Typography.Text type="danger">{item.error_code}：{item.error_message}</Typography.Text>
        : item.drawing_id ? <Typography.Link href="/drawings">图纸 #{item.drawing_id}</Typography.Link> : <Typography.Text type="secondary">{item.role === 'source_dwg' ? `将配对为 ${item.normalized_stem}.dxf` : '批次级数据表'}</Typography.Text>,
    },
  ];

  const excelExists = (batch?.counts.excel ?? 0) > 0;
  const stepsCurrent = batch?.status === 'frozen' ? 3 : batch?.freeze_ready ? 3 : batch?.counts.paired ? 2 : batch?.counts.dwg || batch?.counts.excel ? 1 : 0;

  return (
    <Card title={<Space><CloudUploadOutlined />01 · 文件上传、完整性校验与输入冻结</Space>} style={{ marginTop: 12 }} loading={batchQ.isLoading} extra={<Button icon={<ReloadOutlined />} loading={batchQ.isFetching} onClick={() => batchQ.refetch()}>刷新状态</Button>}>
      <Steps size="small" current={stepsCurrent} items={[{ title: '建立批次' }, { title: '上传源文件' }, { title: '服务器转 DXF' }, { title: '确认冻结' }]} />
      <Alert style={{ marginTop: 18 }} type={batch?.status === 'frozen' ? 'success' : 'info'} showIcon message={batch?.status === 'frozen' ? '输入版本已冻结' : '统一选择并上传一个完整生产文件夹'} description={batch?.status === 'frozen' ? `版本 v${batch.version} · 清单 ${batch.manifest_sha256}` : '文件夹只能包含至少一个 DWG 和恰好一个 Excel；不得包含 DXF 或其他格式。服务器整批审核通过后才入库，随后统一将 DWG 转为 DXF。'} />

      <Row gutter={[12, 12]} style={{ marginTop: 16 }}>
        <Col xs={24} md={8}><Card size="small"><Typography.Text type="secondary">DWG 源文件</Typography.Text><Typography.Title level={3} style={{ margin: '4px 0' }}>{batch?.counts.dwg ?? 0}</Typography.Title><Typography.Text type="secondary">至少 1 个，可多选</Typography.Text></Card></Col>
        <Col xs={24} md={8}><Card size="small"><Typography.Text type="secondary">Excel 数据表</Typography.Text><Typography.Title level={3} style={{ margin: '4px 0' }}>{batch?.counts.excel ?? 0} / 1</Typography.Title><Typography.Text type="secondary">支持 XLS、XLSX</Typography.Text></Card></Col>
        <Col xs={24} md={8}><Card size="small"><Typography.Text type="secondary">DWG / DXF 已配对</Typography.Text><Typography.Title level={3} style={{ margin: '4px 0' }}>{batch?.counts.paired ?? 0} / {batch?.counts.dwg ?? 0}</Typography.Title><Typography.Text type="secondary">由服务器生成</Typography.Text></Card></Col>
      </Row>

      {editable && <Space wrap style={{ marginTop: 16 }}>
        <input ref={folderInput} type="file" multiple hidden {...{ webkitdirectory: '', directory: '' }} onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ''; handleFolder(files); }} />
        <Button type="primary" icon={<CloudUploadOutlined />} loading={uploadFolderM.isPending} disabled={Boolean(batch?.items.length)} onClick={() => folderInput.current?.click()}>选择并上传生产文件夹</Button>
        {Boolean(batch?.items.length) && <Popconfirm title="清空整个生产输入文件夹？" description="冻结前可整批清空后重新选择；不支持逐个替换。" onConfirm={() => clearM.mutate()}><Button danger loading={clearM.isPending}>整批清空</Button></Popconfirm>}
        <Button icon={<SyncOutlined spin={convertM.isPending} />} loading={convertM.isPending} disabled={uploadFolderM.isPending || !batch?.counts.dwg || !excelExists} onClick={() => convertM.mutate()}>{batch?.counts.failed ? '重试失败转换' : '生成并校验 DXF'}</Button>
        <Popconfirm title="确认冻结本批输入？" description="冻结后不可修改；系统将创建图纸处理单元并进入下一阶段。" okText="确认冻结" cancelText="继续检查" onConfirm={() => freezeM.mutate()}><Button type="primary" icon={<LockOutlined />} loading={freezeM.isPending} disabled={!batch?.freeze_ready}>冻结输入版本</Button></Popconfirm>
      </Space>}
      <div aria-live="polite">
        {batch?.issues.map((issue) => <Alert key={`${issue.item_id ?? 'batch'}-${issue.code}`} style={{ marginTop: 12 }} type="error" showIcon message={`${issue.file_name ? `${issue.file_name} · ` : ''}${issue.code}`} description={<><div>{issue.message}</div><Typography.Text strong>建议：{issue.recommended_action}</Typography.Text></>} />)}
      </div>

      <Table style={{ marginTop: 16 }} rowKey="id" size="small" pagination={false} dataSource={batch?.items ?? []} columns={columns} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未上传生产输入" /> }} scroll={{ x: 880 }} />
      {batch?.status === 'frozen' && <Descriptions size="small" bordered column={2} style={{ marginTop: 16 }} items={[{ key: 'version', label: '冻结版本', children: `v${batch.version}` }, { key: 'time', label: '冻结时间', children: batch.frozen_at ? fmtDateTime(batch.frozen_at) : '—' }, { key: 'manifest', label: '清单 SHA-256', span: 2, children: <Typography.Text copyable code>{batch.manifest_sha256}</Typography.Text> }, { key: 'drawings', label: '图纸处理单元', span: 2, children: <Space wrap>{batch.items.filter((item) => item.drawing_id).map((item) => <Typography.Link key={item.id} href="/drawings">{item.normalized_stem} · #{item.drawing_id}</Typography.Link>)}</Space> }]} />}
      {batch?.freeze_ready && batch.status !== 'frozen' && <Alert style={{ marginTop: 12 }} type="success" showIcon icon={<CheckCircleOutlined />} message="完整性检查通过，可以冻结" description="冻结操作会再次读取对象并核对大小、SHA-256、真实格式和 DWG/DXF 文件名配对。" />}
    </Card>
  );
}
