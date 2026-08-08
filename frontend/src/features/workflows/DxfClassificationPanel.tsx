import { useMemo, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
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
  FolderOpenOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  downloadAllDxfClassificationArchive,
  downloadDxfClassificationFile,
  downloadDxfClassificationGroupArchive,
  executeWorkflowStage,
  getDxfClassification,
  getDxfClassificationGroup,
  getWorkflow,
} from './workflows.api';
import {
  describeDownloadError,
  operatorErrorMessage,
  useDownload,
  type TransferProgress,
} from '../../shared/api';
import { ApiErrorAlert, CancellableDownloadProgress, fmtSize } from '../../shared/components';
import type {
  DxfClassificationGroup,
  DxfClassificationGroupItem,
  WorkflowStage,
} from './workflow';

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

const DIAGNOSTICS: Record<string, string> = {
  TITLE_PROFILE_PROVED: '标题栏规格已确认',
  PROFILE_TYPE_AUTO_DISCOVERED: '新类型由明确规格自动发现',
  TITLE_FIELD_MISSING: '未找到有效的截面字段',
  TITLE_VALUE_MISSING: '截面字段没有可解析的规格',
  TITLE_VALUE_CONFLICT: '标题栏存在多个冲突规格',
  DXF_READ_FAILED: 'DXF 文件无法读取',
};

function sourceLabel(group: DxfClassificationGroup) {
  if (group.disposition === 'review_required') return '需要处理';
  if (group.disposition === 'unreadable') return '读取失败';
  if (group.type_source === 'auto_discovered') return '自动发现';
  if (group.type_source === 'legacy') return '历史记录';
  return '内置类型';
}

function sourceColor(group: DxfClassificationGroup) {
  if (group.disposition === 'review_required') return 'warning';
  if (group.disposition === 'unreadable') return 'error';
  if (group.type_source === 'auto_discovered') return 'processing';
  return 'default';
}

export function DxfClassificationPanel({
  workflowId,
  stage,
  isCurrent,
  onChanged,
}: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const downloadCtrl = useDownload();
  const [selectedGroupKey, setSelectedGroupKey] = useState<string>();
  const [detailPage, setDetailPage] = useState(1);
  const [downloadProgress, setDownloadProgress] = useState<{
    label: string;
    progress: TransferProgress;
  } | null>(null);

  const runQ = useQuery({
    queryKey: ['workflow-dxf-classification', workflowId],
    queryFn: () => getDxfClassification(workflowId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'running'
        || stage?.status === 'queued'
        || stage?.status === 'running'
        ? 4000
        : false;
    },
  });
  const run = runQ.data;
  const selectedGroup = useMemo(
    () => run?.groups.find((group) => group.group_key === selectedGroupKey),
    [run?.groups, selectedGroupKey],
  );
  const groupQ = useQuery({
    queryKey: [
      'workflow-dxf-classification-group',
      workflowId,
      selectedGroupKey,
      detailPage,
    ],
    queryFn: () => getDxfClassificationGroup(
      workflowId,
      selectedGroupKey ?? '',
      detailPage,
      20,
    ),
    enabled: Boolean(selectedGroupKey),
  });

  const executeMutation = useMutation({
    mutationFn: async () => {
      const workflow = await getWorkflow(workflowId);
      if (!isCurrent || workflow.current_stage !== 'dxf_classification') {
        throw new Error('当前阶段已变化，请刷新后重试');
      }
      return executeWorkflowStage(workflowId, 'dxf_classification', {
        execution_kind: 'steel_dxf_classification',
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['workflow-dxf-classification', workflowId],
      });
      onChanged();
    },
  });
  const allDownload = useMutation({
    mutationFn: () => {
      const handle = downloadCtrl.start();
      return downloadAllDxfClassificationArchive(
        workflowId,
        (progress) => setDownloadProgress({ label: '全部分类图纸下载', progress }),
        run?.groups.reduce((total, group) => total + group.total_size_bytes, 0),
        handle.signal,
      ).finally(handle.finish);
    },
    onError: (error) => {
      const result = describeDownloadError(error, '全部 DXF 下载失败');
      setDownloadProgress(null);
      if (result.cancelled) {
        message.info('下载已取消');
      } else {
        message.error(result.message);
      }
    },
  });
  const groupDownload = useMutation({
    mutationFn: (group: DxfClassificationGroup) => {
      const handle = downloadCtrl.start();
      return downloadDxfClassificationGroupArchive(
        workflowId,
        group.group_key,
        (progress) => setDownloadProgress({ label: `${group.label} 类图纸下载`, progress }),
        group.total_size_bytes,
        handle.signal,
      ).finally(handle.finish);
    },
    onError: (error) => {
      const result = describeDownloadError(error, '分类文件夹下载失败');
      setDownloadProgress(null);
      if (result.cancelled) {
        message.info('下载已取消');
      } else {
        message.error(result.message);
      }
    },
  });
  const singleFileDownload = useMutation({
    mutationFn: ({ groupKey, outputName }: { groupKey: string; outputName: string }) => {
      const handle = downloadCtrl.start();
      return downloadDxfClassificationFile(
        workflowId,
        groupKey,
        outputName,
        (progress) => setDownloadProgress({ label: `下载 ${outputName}`, progress }),
        handle.signal,
      ).finally(handle.finish);
    },
    onSuccess: (_data, vars) => message.success(`已下载 ${vars.outputName}`),
    onError: (error) => {
      const result = describeDownloadError(error, '分类 DXF 下载失败');
      setDownloadProgress(null);
      if (result.cancelled) {
        message.info('下载已取消');
      } else {
        message.error(result.message);
      }
    },
  });

  const canExecute = isCurrent
    && !runQ.isError
    && ['waiting_input', 'ready', 'failed'].includes(stage?.status ?? '');
  const active = ['queued', 'running'].includes(stage?.status ?? '')
    || run?.status === 'running';
  const warningCount = (run?.review_required_count ?? 0) + (run?.unreadable_count ?? 0);

  const openGroup = (group: DxfClassificationGroup) => {
    setDetailPage(1);
    setSelectedGroupKey(group.group_key);
  };

  const detailColumns = [
    {
      title: 'DXF 文件',
      dataIndex: 'output_name',
      render: (value: string) => (
        <Typography.Text className="workflow-classification-file-name">
          {value}
        </Typography.Text>
      ),
    },
    {
      title: '规格',
      render: (_: unknown, item: DxfClassificationGroupItem) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text>{item.profile_raw || '—'}</Typography.Text>
          {item.profile_normalized && item.profile_normalized !== item.profile_raw && (
            <Typography.Text type="secondary">{item.profile_normalized}</Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '类型',
      render: (_: unknown, item: DxfClassificationGroupItem) => (
        <Space wrap>
          {item.part_type && <Tag color="blue">{item.part_type}</Tag>}
          {item.type_source === 'auto_discovered' && <Tag color="processing">自动发现</Tag>}
          {item.type_source === 'catalog' && <Tag>内置类型</Tag>}
          {item.type_source === 'legacy' && <Tag>历史记录</Tag>}
        </Space>
      ),
    },
    {
      title: '状态与诊断',
      render: (_: unknown, item: DxfClassificationGroupItem) => {
        const status = DISPOSITION[item.disposition];
        return (
          <Space orientation="vertical" size={2}>
            <Tag color={status.color}>{status.label}</Tag>
            {item.diagnostics.map((diagnostic) => (
              <Typography.Text type="secondary" key={diagnostic}>
                {DIAGNOSTICS[diagnostic] ?? '存在未识别的分类依据，请人工核对'}
              </Typography.Text>
            ))}
          </Space>
        );
      },
    },
    {
      title: '大小',
      dataIndex: 'size_bytes',
      width: 100,
      render: (value: number) => fmtSize(value),
    },
    {
      title: '操作',
      key: 'actions',
      width: 110,
      render: (_: unknown, item: DxfClassificationGroupItem) => (
        <Button
          type="text"
          size="small"
          icon={<DownloadOutlined />}
          aria-label={`下载 ${item.output_name}`}
          loading={
            singleFileDownload.isPending
            && singleFileDownload.variables?.outputName === item.output_name
          }
          disabled={
            downloadCtrl.active
            && !(singleFileDownload.isPending
              && singleFileDownload.variables?.outputName === item.output_name)
          }
          onClick={() => singleFileDownload.mutate({
            groupKey: selectedGroupKey!,
            outputName: item.output_name,
          })}
        >
          下载
        </Button>
      ),
    },
  ];

  return (
    <Card
      className="workflow-classification-panel"
      title={<Space><FileSearchOutlined />02 · DXF 预处理与分类分流</Space>}
      style={{ marginTop: 12 }}
    >
      {runQ.isError && (
        <ApiErrorAlert
          title="分类批次读取失败"
          error={runQ.error}
          fallback="分类批次读取失败"
          retryLabel="重新读取"
          retryLoading={runQ.isFetching}
          onRetry={() => runQ.refetch()}
        />
      )}
      {!runQ.isError && !isCurrent && !run && (
        <Alert
          type="info"
          showIcon
          message="等待输入冻结"
          description="服务器完成 DWG→DXF 和输入冻结后，才会开放分类分流。"
        />
      )}
      {!runQ.isError && isCurrent && !run && !active && (
        <Alert
          type="info"
          showIcon
          message="冻结 DXF 已就绪"
          description="系统将自动添加“_拆板前”后缀，按标题栏截面字段分流；不需要选择文件或填写路径。"
          action={(
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={executeMutation.isPending}
              onClick={() => executeMutation.mutate()}
            >
              开始 DXF 分类分流
            </Button>
          )}
        />
      )}
      {active && (
        <Alert
          type="info"
          showIcon
          message={`分类任务${stage?.job_id ? ` #${stage.job_id}` : ''}正在执行`}
          description={(
            <Progress
              percent={stage?.progress ?? run?.job.progress ?? 0}
              status="active"
            />
          )}
          action={(
            <Button
              icon={<ReloadOutlined />}
              loading={runQ.isFetching}
              onClick={() => runQ.refetch()}
            >
              刷新
            </Button>
          )}
        />
      )}
      {run?.status === 'failed' && (
        <Alert
          type="error"
          showIcon
          message="图纸分类未完成"
          description={operatorErrorMessage(
            run.error_code,
            run.error_message,
            '请核对本批输入图纸和待确认项后重新分类。',
          )}
          action={canExecute && (
            <Button
              type="primary"
              danger
              loading={executeMutation.isPending}
              onClick={() => executeMutation.mutate()}
            >
              重试分类
            </Button>
          )}
        />
      )}
      {executeMutation.error && (
        <ApiErrorAlert
          title="提交分类任务失败"
          error={executeMutation.error}
          fallback="分类任务提交失败"
        />
      )}
      {run && ['completed', 'completed_with_review'].includes(run.status) && (
        <>
          <div className="workflow-classification-command">
            <div>
              <Typography.Text strong>
                Steel DXF Classifier {run.classifier_version}
              </Typography.Text>
              <Typography.Text type="secondary">
                输入清单 {run.input_manifest_sha256.slice(0, 12)}…
              </Typography.Text>
            </div>
            <Space wrap>
              <Button
                icon={<ReloadOutlined />}
                loading={runQ.isFetching}
                onClick={() => runQ.refetch()}
              >
                刷新
              </Button>
              <Button
                type="primary"
                icon={<DownloadOutlined />}
                loading={allDownload.isPending}
                disabled={downloadCtrl.active && !allDownload.isPending}
                onClick={() => allDownload.mutate()}
              >
                下载全部 DXF
              </Button>
            </Space>
          </div>

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
          {downloadProgress && (
            <CancellableDownloadProgress
              label={downloadProgress.label}
              progress={downloadProgress.progress}
              active={downloadCtrl.active}
              onCancel={() => {
                downloadCtrl.cancel();
                setDownloadProgress(null);
              }}
            />
          )}

          {warningCount > 0 && (
            <Alert
              className="workflow-classification-warning"
              type="warning"
              showIcon
              icon={<WarningOutlined />}
              message={`${warningCount} 张图纸需要处理`}
              description={`${run.review_required_count} 张分类不确定，${run.unreadable_count} 张无法读取；已确定的图纸仍可进入下一阶段。`}
              action={(
                <Space wrap>
                  {run.groups
                    .filter((group) => group.disposition !== 'classified')
                    .map((group) => (
                      <Button key={group.group_key} onClick={() => openGroup(group)}>
                        查看{group.label}
                      </Button>
                    ))}
                </Space>
              )}
            />
          )}

          <div className="workflow-classification-folders">
            {run.groups.map((group) => (
              <div
                className={[
                  'workflow-classification-folder',
                  group.disposition !== 'classified' ? 'is-warning' : '',
                  group.type_source === 'auto_discovered' ? 'is-discovered' : '',
                ].filter(Boolean).join(' ')}
                key={group.group_key}
              >
                <button
                  type="button"
                  className="workflow-classification-folder-open"
                  aria-label={`${group.label} ${group.count} 张 ${sourceLabel(group)}`}
                  onClick={() => openGroup(group)}
                >
                  <FolderOpenOutlined />
                  <span>
                    <strong>{group.label}</strong>
                    <small>{group.count} 张 · {fmtSize(group.total_size_bytes)}</small>
                  </span>
                  <Tag color={sourceColor(group)}>{sourceLabel(group)}</Tag>
                </button>
                <Button
                  type="text"
                  icon={<DownloadOutlined />}
                  aria-label={`下载 ${group.label} 类 DXF`}
                  loading={
                    groupDownload.isPending
                    && groupDownload.variables?.group_key === group.group_key
                  }
                  disabled={
                    downloadCtrl.active
                    && !(groupDownload.isPending
                      && groupDownload.variables?.group_key === group.group_key)
                  }
                  onClick={() => groupDownload.mutate(group)}
                >
                  下载本类
                </Button>
              </div>
            ))}
          </div>
        </>
      )}

      <Drawer
        title={selectedGroup ? `${selectedGroup.label} · ${selectedGroup.count} 张 DXF` : '分类明细'}
        open={Boolean(selectedGroup)}
        width={860}
        onClose={() => setSelectedGroupKey(undefined)}
        extra={selectedGroup && (
          <Button
            icon={<DownloadOutlined />}
            aria-label={`下载 ${selectedGroup.label} 类 DXF`}
            loading={
              groupDownload.isPending
              && groupDownload.variables?.group_key === selectedGroup.group_key
            }
            disabled={
              downloadCtrl.active
              && !(groupDownload.isPending
                && groupDownload.variables?.group_key === selectedGroup.group_key)
            }
            onClick={() => groupDownload.mutate(selectedGroup)}
          >
            下载本类
          </Button>
        )}
      >
        {groupQ.isError && (
          <ApiErrorAlert
            title="分类文件夹加载失败"
            error={groupQ.error}
            fallback="分类文件夹加载失败"
            retryLabel="重新读取"
            retryLoading={groupQ.isFetching}
            onRetry={() => groupQ.refetch()}
          />
        )}
        <Table<DxfClassificationGroupItem>
          rowKey="output_name"
          loading={groupQ.isLoading}
          dataSource={groupQ.data?.items ?? []}
          columns={detailColumns}
          pagination={{
            current: groupQ.data?.page ?? detailPage,
            pageSize: groupQ.data?.page_size ?? 20,
            total: groupQ.data?.total ?? 0,
            showSizeChanger: false,
            onChange: setDetailPage,
          }}
          scroll={{ x: 760 }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="该分类文件夹暂无明细"
              />
            ),
          }}
        />
      </Drawer>
    </Card>
  );
}
