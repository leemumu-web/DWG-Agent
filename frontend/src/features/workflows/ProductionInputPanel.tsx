import { useRef, useState } from 'react';
import {
  CheckCircleOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
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
import { downloadFile, uploadFile } from '../files';
import {
  createWorkflowInputBatch,
  freezeWorkflowInputBatch,
  getWorkflowInputBatch,
  registerWorkflowInputFile,
  removeWorkflowInputFile,
  requestWorkflowInputConversions,
} from './workflow-inputs.api';
import { fmtDateTime, fmtSize } from '../../shared/components';
import type { StoredFile } from '../files';
import type { WorkflowInputBatch, WorkflowInputItem } from './workflow-input';

interface OrphanUpload {
  file: StoredFile;
  error: string;
}

const ACTIVE_BATCH = new Set(['converting']);
const ACTIVE_JOB = new Set(['queued', 'running', 'retrying']);
const INPUT_DXF_NOT_ALLOWED = 'INPUT_DXF_NOT_ALLOWED';

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
  const dwgInput = useRef<HTMLInputElement>(null);
  const excelInput = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({ done: 0, total: 0 });
  const [orphans, setOrphans] = useState<OrphanUpload[]>([]);

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

  const removeM = useMutation({
    mutationFn: (itemId: number) => removeWorkflowInputFile(workflowId, itemId),
    onSuccess: () => { message.success('已从生产批次移除，文件中心原文件仍保留'); refresh(); },
    onError: (error) => message.error(describeApiError(error, '移除失败')),
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
  const retryRegisterM = useMutation({
    mutationFn: (stored: StoredFile) => registerWorkflowInputFile(workflowId, stored.id),
    onSuccess: (result, stored) => {
      setOrphans((items) => items.filter((item) => item.file.id !== stored.id));
      refresh(result.batch);
      message.success(`${stored.original_name} 已补登记`);
    },
    onError: (error) => message.error(describeApiError(error, '补登记失败')),
  });

  const uploadAndRegister = async (file: File) => {
    const requestKey = crypto.randomUUID();
    const stored = await uploadFile(file, `workflow-input-${batch!.id}`, requestKey);
    try {
      const result = await registerWorkflowInputFile(workflowId, stored.id);
      queryClient.setQueryData(['workflow-input-batch', workflowId], result.batch);
    } catch (error) {
      const text = describeApiError(error, '登记失败');
      setOrphans((items) => [...items.filter((item) => item.file.id !== stored.id), { file: stored, error: text }]);
      throw new Error(`${file.name} 已存入文件中心，但未进入生产批次：${text}`);
    }
  };

  const handleFiles = async (selected: File[], kind: 'dwg' | 'excel') => {
    if (!batch || !editable) return;
    const allowed = kind === 'dwg' ? ['.dwg'] : ['.xls', '.xlsx'];
    const invalid = selected.find((file) => !allowed.some((ext) => file.name.toLowerCase().endsWith(ext)));
    if (invalid) {
      const isDxf = invalid.name.toLowerCase().endsWith('.dxf');
      message.error(isDxf
        ? `${INPUT_DXF_NOT_ALLOWED}：请只上传 DWG，DXF 由服务器生成`
        : `${invalid.name} 格式不支持`);
      return;
    }
    if (kind === 'excel' && (batch.counts.excel > 0 || selected.length !== 1)) {
      message.error('每批必须且只能有一个 Excel；请先移除现有 Excel 再替换');
      return;
    }
    setUploading(true);
    setUploadProgress({ done: 0, total: selected.length });
    const failures: string[] = [];
    let cursor = 0;
    const worker = async () => {
      while (cursor < selected.length) {
        const file = selected[cursor++];
        try { await uploadAndRegister(file); }
        catch (error) { failures.push(error instanceof Error ? error.message : String(error)); }
        setUploadProgress((progress) => ({ ...progress, done: progress.done + 1 }));
      }
    };
    await Promise.all(Array.from({ length: Math.min(3, selected.length) }, worker));
    setUploading(false);
    refresh();
    if (failures.length) message.error(`${failures.length} 个文件未完成登记，请按下方提示重试`);
    else message.success(`${selected.length} 个文件上传并校验完成`);
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
      render: (_: unknown, item: WorkflowInputItem) => <div>{itemStatus(item)}{item.conversion_job && <><Progress percent={item.conversion_job.progress ?? 0} size="small" /><Typography.Text type="secondary" style={{ fontSize: 12 }}>任务 #{item.conversion_job.id} · 尝试 #{item.conversion_job.attempt}</Typography.Text></>}{item.derived_dxf && <div><Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => downloadFile(item.derived_dxf!.id, item.derived_dxf!.original_name)}>下载生成 DXF</Button></div>}</div>,
    },
    {
      title: '反馈', key: 'feedback',
      render: (_: unknown, item: WorkflowInputItem) => item.error_code
        ? <Typography.Text type="danger">{item.error_code}：{item.error_message}</Typography.Text>
        : item.drawing_id ? <Typography.Link href="/drawings">图纸 #{item.drawing_id}</Typography.Link> : <Typography.Text type="secondary">{item.role === 'source_dwg' ? `将配对为 ${item.normalized_stem}.dxf` : '批次级数据表'}</Typography.Text>,
    },
    {
      title: '操作', key: 'actions', width: 88,
      render: (_: unknown, item: WorkflowInputItem) => editable && <Popconfirm title="从本生产批次移除？" description="文件中心中的原文件不会删除。" onConfirm={() => removeM.mutate(item.id)}><Button danger type="text" icon={<DeleteOutlined />}>移除</Button></Popconfirm>,
    },
  ];

  const excelExists = (batch?.counts.excel ?? 0) > 0;
  const stepsCurrent = batch?.status === 'frozen' ? 3 : batch?.freeze_ready ? 3 : batch?.counts.paired ? 2 : batch?.counts.dwg || batch?.counts.excel ? 1 : 0;

  return (
    <Card title={<Space><CloudUploadOutlined />01 · 文件上传、完整性校验与输入冻结</Space>} style={{ marginTop: 12 }} loading={batchQ.isLoading} extra={<Button icon={<ReloadOutlined />} loading={batchQ.isFetching} onClick={() => batchQ.refetch()}>刷新状态</Button>}>
      <Steps size="small" current={stepsCurrent} items={[{ title: '建立批次' }, { title: '上传源文件' }, { title: '服务器转 DXF' }, { title: '确认冻结' }]} />
      <Alert style={{ marginTop: 18 }} type={batch?.status === 'frozen' ? 'success' : 'info'} showIcon message={batch?.status === 'frozen' ? '输入版本已冻结' : '只需上传多个 DWG 和一个 Excel'} description={batch?.status === 'frozen' ? `版本 v${batch.version} · 清单 ${batch.manifest_sha256}` : '不要人工上传 DXF：服务器会调用现有转换技术生成、校验并与同名 DWG 配对。文件名请保持唯一，上传后仍可在冻结前移除和更换。'} />

      <Row gutter={[12, 12]} style={{ marginTop: 16 }}>
        <Col xs={24} md={8}><Card size="small"><Typography.Text type="secondary">DWG 源文件</Typography.Text><Typography.Title level={3} style={{ margin: '4px 0' }}>{batch?.counts.dwg ?? 0}</Typography.Title><Typography.Text type="secondary">至少 1 个，可多选</Typography.Text></Card></Col>
        <Col xs={24} md={8}><Card size="small"><Typography.Text type="secondary">Excel 数据表</Typography.Text><Typography.Title level={3} style={{ margin: '4px 0' }}>{batch?.counts.excel ?? 0} / 1</Typography.Title><Typography.Text type="secondary">支持 XLS、XLSX</Typography.Text></Card></Col>
        <Col xs={24} md={8}><Card size="small"><Typography.Text type="secondary">DWG / DXF 已配对</Typography.Text><Typography.Title level={3} style={{ margin: '4px 0' }}>{batch?.counts.paired ?? 0} / {batch?.counts.dwg ?? 0}</Typography.Title><Typography.Text type="secondary">由服务器生成</Typography.Text></Card></Col>
      </Row>

      {editable && <Space wrap style={{ marginTop: 16 }}>
        <input ref={dwgInput} type="file" accept=".dwg" multiple hidden onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ''; void handleFiles(files, 'dwg'); }} />
        <input ref={excelInput} type="file" accept=".xls,.xlsx" hidden onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ''; void handleFiles(files, 'excel'); }} />
        <Button type="primary" icon={<CloudUploadOutlined />} disabled={uploading} onClick={() => dwgInput.current?.click()}>上传 DWG（可多选）</Button>
        <Button icon={<FileExcelOutlined />} disabled={uploading || excelExists} title={excelExists ? '每批只能有一个 Excel；移除后可替换' : undefined} onClick={() => excelInput.current?.click()}>上传 Excel</Button>
        <Button icon={<SyncOutlined spin={convertM.isPending} />} loading={convertM.isPending} disabled={uploading || !batch?.counts.dwg || !excelExists} onClick={() => convertM.mutate()}>{batch?.counts.failed ? '重试失败转换' : '生成并校验 DXF'}</Button>
        <Popconfirm title="确认冻结本批输入？" description="冻结后不可修改；系统将创建图纸处理单元并进入下一阶段。" okText="确认冻结" cancelText="继续检查" onConfirm={() => freezeM.mutate()}><Button type="primary" icon={<LockOutlined />} loading={freezeM.isPending} disabled={!batch?.freeze_ready}>冻结输入版本</Button></Popconfirm>
      </Space>}
      {uploading && <Progress style={{ marginTop: 12 }} percent={Math.round((uploadProgress.done / Math.max(1, uploadProgress.total)) * 100)} format={() => `${uploadProgress.done}/${uploadProgress.total}`} />}

      <div aria-live="polite">
        {orphans.map((orphan) => <Alert key={orphan.file.id} style={{ marginTop: 12 }} type="warning" showIcon message={`${orphan.file.original_name} 已存入文件中心，但未进入生产批次`} description={orphan.error} action={<Button loading={retryRegisterM.isPending} onClick={() => retryRegisterM.mutate(orphan.file)}>仅重试登记</Button>} />)}
        {batch?.issues.map((issue) => <Alert key={`${issue.item_id ?? 'batch'}-${issue.code}`} style={{ marginTop: 12 }} type="error" showIcon message={`${issue.file_name ? `${issue.file_name} · ` : ''}${issue.code}`} description={<><div>{issue.message}</div><Typography.Text strong>建议：{issue.recommended_action}</Typography.Text></>} />)}
      </div>

      <Table style={{ marginTop: 16 }} rowKey="id" size="small" pagination={false} dataSource={batch?.items ?? []} columns={columns} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未上传生产输入" /> }} scroll={{ x: 880 }} />
      {batch?.status === 'frozen' && <Descriptions size="small" bordered column={2} style={{ marginTop: 16 }} items={[{ key: 'version', label: '冻结版本', children: `v${batch.version}` }, { key: 'time', label: '冻结时间', children: batch.frozen_at ? fmtDateTime(batch.frozen_at) : '—' }, { key: 'manifest', label: '清单 SHA-256', span: 2, children: <Typography.Text copyable code>{batch.manifest_sha256}</Typography.Text> }, { key: 'drawings', label: '图纸处理单元', span: 2, children: <Space wrap>{batch.items.filter((item) => item.drawing_id).map((item) => <Typography.Link key={item.id} href="/drawings">{item.normalized_stem} · #{item.drawing_id}</Typography.Link>)}</Space> }]} />}
      {batch?.freeze_ready && batch.status !== 'frozen' && <Alert style={{ marginTop: 12 }} type="success" showIcon icon={<CheckCircleOutlined />} message="完整性检查通过，可以冻结" description="冻结操作会再次读取对象并核对大小、SHA-256、真实格式和 DWG/DXF 文件名配对。" />}
    </Card>
  );
}
